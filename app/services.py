"""Logique métier partagée : journal, statistiques de présence, import des utilisateurs."""
import csv
import io
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import config
from .models import MOT_DE_PASSE_NON_DEFINI, ROLES, CahierEntry, Journal, Module, Presence, Seance, User
from .notifications import notifier
from .security import normaliser_email, normaliser_telephone


def journaliser(db: Session, user: User | None, action: str, detail: str = "") -> None:
    db.add(Journal(user_id=user.id if user else None, action=action, detail=detail))


# --- Recherche d'utilisateurs ------------------------------------------------

def trouver_par_identifiant(db: Session, identifiant: str) -> User | None:
    identifiant = (identifiant or "").strip()
    if not identifiant:
        return None
    conditions = [User.matricule == identifiant.upper()]
    email = normaliser_email(identifiant)
    if email:
        conditions.append(User.email == email)
    tel = normaliser_telephone(identifiant) if any(c.isdigit() for c in identifiant) else None
    if tel:
        conditions.append(User.telephone == tel)
    return db.scalars(select(User).where(or_(*conditions))).first()


def etudiants_de_classe(db: Session, classe: str) -> list[User]:
    return list(db.scalars(
        select(User).where(User.role == "etudiant", User.classe == classe, User.actif.is_(True))
        .order_by(User.nom, User.prenom)
    ))


def classes(db: Session) -> list[str]:
    valeurs = set(db.scalars(select(User.classe).where(User.classe.is_not(None))))
    valeurs |= set(db.scalars(select(Module.classe)))
    return sorted(v for v in valeurs if v)


# --- Statistiques -------------------------------------------------------------

@dataclass
class StatsEtudiant:
    seances: int = 0
    presents: int = 0
    absences: int = 0
    non_justifiees: int = 0
    par_module: dict[str, list[int]] = field(default_factory=dict)  # nom -> [présents, total]

    @property
    def taux(self) -> int:
        return round(100 * self.presents / self.seances) if self.seances else 100

    @property
    def alerte(self) -> bool:
        return self.seances > 0 and self.taux < config.SEUIL_ALERTE

    def modules(self) -> list[tuple[str, int]]:
        return [(nom, round(100 * p / t) if t else 100) for nom, (p, t) in sorted(self.par_module.items())]


def stats_etudiant(db: Session, etudiant: User) -> StatsEtudiant:
    stats = StatsEtudiant()
    rows = db.execute(
        select(Presence.statut, Presence.justif_statut, Module.nom)
        .join(Seance, Presence.seance_id == Seance.id)
        .join(Module, Seance.module_id == Module.id)
        .where(Presence.etudiant_id == etudiant.id, Seance.appel_statut == "clos")
    ).all()
    for statut, justif, module in rows:
        stats.seances += 1
        compteur = stats.par_module.setdefault(module, [0, 0])
        compteur[1] += 1
        if statut == "present":
            stats.presents += 1
            compteur[0] += 1
        else:
            stats.absences += 1
            if justif != "accepte":
                stats.non_justifiees += 1
    return stats


def taux_global(db: Session, depuis: date | None = None) -> int | None:
    q = select(Presence.statut, func.count()).join(Seance).where(Seance.appel_statut == "clos")
    if depuis:
        q = q.where(Seance.date >= depuis)
    compte = dict(db.execute(q.group_by(Presence.statut)).all())
    total = sum(compte.values())
    return round(100 * compte.get("present", 0) / total) if total else None


def seances_sans_cahier(db: Session, enseignant: User | None = None) -> list[Seance]:
    q = (
        select(Seance).join(Module).outerjoin(CahierEntry, CahierEntry.seance_id == Seance.id)
        .where(CahierEntry.id.is_(None), Seance.date < date.today(), Seance.date >= date.today() - timedelta(days=30))
        .order_by(Seance.date.desc())
    )
    if enseignant:
        q = q.where(Module.enseignant_id == enseignant.id)
    return list(db.scalars(q))


def debut_semaine(jour: date) -> date:
    return jour - timedelta(days=jour.weekday())


# --- Import des étudiants et enseignants --------------------------------------

COLONNES_IMPORT = ["matricule", "nom", "prenom", "role", "email", "telephone", "classe"]
_ALIAS = {
    "prénom": "prenom", "téléphone": "telephone", "tel": "telephone", "mail": "email",
    "e-mail": "email", "rôle": "role", "numero": "telephone", "numéro": "telephone",
}
_ROLES_ALIAS = {
    "etudiant": "etudiant", "étudiant": "etudiant", "eleve": "etudiant", "élève": "etudiant",
    "enseignant": "enseignant", "professeur": "enseignant", "prof": "enseignant",
    "admin": "admin", "administration": "admin", "scolarite": "admin", "scolarité": "admin",
}


@dataclass
class RapportImport:
    crees: int = 0
    mis_a_jour: int = 0
    erreurs: list[str] = field(default_factory=list)


def lire_fichier_import(nom_fichier: str, contenu: bytes) -> list[dict]:
    if nom_fichier.lower().endswith(".xlsx"):
        from openpyxl import load_workbook

        feuille = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True).active
        lignes = [["" if v is None else str(v) for v in row] for row in feuille.iter_rows(values_only=True)]
    else:
        texte = contenu.decode("utf-8-sig", errors="replace")
        separateur = ";" if texte.count(";") > texte.count(",") else ","
        lignes = list(csv.reader(io.StringIO(texte), delimiter=separateur))
    if not lignes:
        return []
    entetes = [_ALIAS.get(h.strip().lower(), h.strip().lower()) for h in lignes[0]]
    return [
        {entetes[i]: (v or "").strip() for i, v in enumerate(ligne) if i < len(entetes)}
        for ligne in lignes[1:] if any((v or "").strip() for v in ligne)
    ]


def creer_ou_maj_utilisateur(db: Session, data: dict, rapport: RapportImport, ligne: int) -> User | None:
    """Crée ou met à jour un compte (sans valider la transaction).

    Retourne le nouvel utilisateur (None pour une mise à jour), afin que l'appelant
    envoie l'invitation seulement après l'enregistrement en base.
    """
    role = _ROLES_ALIAS.get((data.get("role") or "etudiant").strip().lower())
    nom, prenom = data.get("nom", "").strip(), data.get("prenom", "").strip()
    email = normaliser_email(data.get("email"))
    tel = normaliser_telephone(data.get("telephone"))
    matricule = (data.get("matricule") or "").strip().upper() or None
    classe = (data.get("classe") or "").strip() or None
    if role not in ROLES:
        rapport.erreurs.append(f"Ligne {ligne} : rôle « {data.get('role')} » inconnu.")
        return
    if not nom or not prenom:
        rapport.erreurs.append(f"Ligne {ligne} : nom et prénom obligatoires.")
        return
    if role == "etudiant" and (not matricule or not classe):
        rapport.erreurs.append(f"Ligne {ligne} : un étudiant doit avoir un matricule et une classe.")
        return

    conditions = [c for c in (
        User.matricule == matricule if matricule else None,
        User.email == email if email else None,
        User.telephone == tel if tel else None,
    ) if c is not None]
    existants = list(db.scalars(select(User).where(or_(*conditions)))) if conditions else []
    if not existants and not email:
        rapport.erreurs.append(f"Ligne {ligne} : e-mail valide obligatoire (les accès sont envoyés par e-mail).")
        return None
    if len(existants) > 1:
        rapport.erreurs.append(f"Ligne {ligne} : matricule, e-mail ou téléphone déjà utilisés par des comptes différents.")
        return

    if existants and matricule and existants[0].matricule and existants[0].matricule != matricule:
        rapport.erreurs.append(
            f"Ligne {ligne} : e-mail ou téléphone déjà utilisé par le matricule {existants[0].matricule}."
        )
        return None
    if existants:
        user = existants[0]
        user.nom, user.prenom, user.role, user.classe = nom, prenom, role, classe
        user.matricule = matricule or user.matricule
        user.email = email or user.email
        user.telephone = tel or user.telephone
        rapport.mis_a_jour += 1
        db.flush()
        return None

    user = User(
        matricule=matricule, nom=nom, prenom=prenom, role=role, email=email, telephone=tel,
        classe=classe, password_hash=MOT_DE_PASSE_NON_DEFINI, must_change_password=False,
    )
    preparer_lien(user)
    db.add(user)
    db.flush()
    rapport.crees += 1
    return user


# --- Liens d'inscription et de réinitialisation ---------------------------------
# Le lien contient l'identifiant du compte et un jeton à usage unique, signés avec la
# clé secrète : il ne peut pas être fabriqué, expire après INVITATION_JOURS jours et
# ne sert qu'une fois (le jeton est effacé dès que le mot de passe est choisi).

_signeur = URLSafeTimedSerializer(config.SECRET_KEY, salt="tdsi-inscription")


def preparer_lien(user: User) -> None:
    user.invitation_nonce = secrets.token_hex(16)


def lien_inscription(user: User) -> str:
    jeton = _signeur.dumps({"u": user.id, "n": user.invitation_nonce})
    return f"{config.BASE_URL}/inscription/{jeton}"


def lire_lien(db: Session, jeton: str) -> User | None:
    try:
        data = _signeur.loads(jeton, max_age=config.INVITATION_JOURS * 86400)
    except (BadSignature, SignatureExpired):
        return None
    user = db.get(User, data.get("u")) if isinstance(data, dict) else None
    if user is None or not user.actif or not user.invitation_nonce or user.invitation_nonce != data.get("n"):
        return None
    return user


def envoyer_invitation(user: User) -> bool:
    """E-mail de bienvenue (compte pas encore activé) ou de réinitialisation du mot de passe."""
    lien = lien_inscription(user)
    if user.compte_active:
        sujet = "Réinitialisation de votre mot de passe TDSI"
        lignes = [
            f"Bonjour {user.prenom},",
            "",
            "Pour choisir un nouveau mot de passe, ouvrez ce lien :",
            lien,
            "",
            f"Il est valable {config.INVITATION_JOURS} jours et ne sert qu'une fois.",
            "Si vous n'avez rien demandé, ignorez ce message.",
        ]
    else:
        sujet = "Activez votre compte TDSI Campus"
        lignes = [
            f"Bonjour {user.prenom},",
            "",
            "Votre compte sur la plateforme de suivi de cours TDSI est prêt.",
            f"Votre identifiant : {user.matricule or user.email}",
            "",
            "Pour activer votre compte et choisir votre propre mot de passe, ouvrez ce lien :",
            lien,
            "",
            f"Ce lien est personnel, valable {config.INVITATION_JOURS} jours et ne sert qu'une fois.",
        ]
    return notifier(user, sujet, "\n".join(lignes))


def importer_utilisateurs(db: Session, lignes: list[dict], auteur: User) -> RapportImport:
    rapport = RapportImport()
    for i, data in enumerate(lignes, start=2):
        # Ligne du modèle laissée vide (seuls le rôle et la classe sont pré-remplis) : on l'ignore.
        if not any((data.get(cle) or "").strip() for cle in ("matricule", "nom", "prenom", "email")):
            continue
        # Chaque ligne est enregistrée séparément : une ligne défectueuse n'arrête pas l'import.
        avant = (rapport.crees, rapport.mis_a_jour)
        try:
            nouveau = creer_ou_maj_utilisateur(db, data, rapport, i)
            db.commit()
        except Exception as exc:
            db.rollback()
            rapport.crees, rapport.mis_a_jour = avant
            rapport.erreurs.append(f"Ligne {i} : enregistrement impossible ({exc.__class__.__name__}).")
            continue
        if nouveau:
            envoyer_invitation(nouveau)
    journaliser(db, auteur, "import", f"{rapport.crees} créés, {rapport.mis_a_jour} mis à jour, {len(rapport.erreurs)} erreurs")
    db.commit()
    return rapport


def maintenant() -> datetime:
    return datetime.now()


def creer_premier_admin() -> None:
    """En ligne : crée le compte de la responsable (TDSI_ADMIN_EMAIL) s'il n'existe aucun admin,
    et lui envoie son lien d'activation. Ne fait rien ensuite."""
    email = normaliser_email(config.ADMIN_EMAIL)
    if not email:
        return
    from .db import SessionLocal

    with SessionLocal() as db:
        if db.scalars(select(User).where(User.role == "admin")).first():
            return
        admin = User(nom="TDSI", prenom="Responsable", role="admin", email=email,
                     password_hash=MOT_DE_PASSE_NON_DEFINI, must_change_password=False)
        preparer_lien(admin)
        db.add(admin)
        journaliser(db, None, "admin_initial", f"Compte administrateur créé pour {email}")
        db.commit()
        envoyer_invitation(admin)
        # Visible seulement dans les journaux du serveur, au cas où l'e-mail n'arriverait pas.
        print(f"[TDSI] Lien d'activation du compte admin {email} : {lien_inscription(admin)}", flush=True)


# --- Import d'un emploi du temps (semaine type répétée sur le semestre) ------------

JOURS_SEMAINE = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
EMPLOI_DU_TEMPS_L3TDSI_S2 = config.BASE_DIR / "app" / "data" / "emploi_du_temps_L3TDSI_S2.csv"


@dataclass
class RapportPlanning:
    modules_crees: int = 0
    seances_creees: int = 0
    deja_presentes: int = 0
    erreurs: list[str] = field(default_factory=list)


def _heure(valeur: str):
    texte = (valeur or "").strip().lower().replace("h", ":")
    if texte.endswith(":"):
        texte += "00"
    return datetime.strptime(texte, "%H:%M").time()


def importer_emploi_du_temps(db: Session, lignes: list[dict], debut: date, fin: date, auteur: User) -> RapportPlanning:
    """Crée les modules manquants puis une séance par créneau et par semaine entre debut et fin."""
    from .models import TYPES_SEANCE

    rapport = RapportPlanning()
    if fin < debut:
        rapport.erreurs.append("La date de fin est avant la date de début.")
        return rapport
    if (fin - debut).days > 200:
        rapport.erreurs.append("Période trop longue (plus de 200 jours).")
        return rapport
    for i, ligne in enumerate(lignes, start=2):
        nom = (ligne.get("module") or "").strip()
        classe = (ligne.get("classe") or "").strip()
        jour = JOURS_SEMAINE.get((ligne.get("jour") or "").strip().lower())
        type_seance = (ligne.get("type") or "CM").strip().upper()
        try:
            h_debut, h_fin = _heure(ligne.get("debut")), _heure(ligne.get("fin"))
        except ValueError:
            h_debut = h_fin = None
        if not nom or not classe or jour is None or h_debut is None or h_fin <= h_debut or type_seance not in TYPES_SEANCE:
            rapport.erreurs.append(f"Ligne {i} : module, classe, jour, horaires ou type invalides.")
            continue
        code = (ligne.get("code") or "").strip() or None
        critere = Module.code == code if code else Module.nom == nom
        module = db.scalars(select(Module).where(critere, Module.classe == classe)).first()
        if module is None:
            module = Module(nom=nom, classe=classe, code=code)
            db.add(module)
            db.flush()
            rapport.modules_crees += 1
        module.nom = nom
        module.enseignant_nom = (ligne.get("enseignant") or "").strip() or module.enseignant_nom
        jour_courant = debut + timedelta(days=(jour - debut.weekday()) % 7)
        while jour_courant <= fin:
            existe = db.scalars(select(Seance).where(
                Seance.module_id == module.id, Seance.date == jour_courant, Seance.debut == h_debut)).first()
            if existe:
                rapport.deja_presentes += 1
            else:
                db.add(Seance(module_id=module.id, date=jour_courant, debut=h_debut, fin=h_fin,
                              salle=(ligne.get("salle") or "").strip(), type=type_seance))
                rapport.seances_creees += 1
            jour_courant += timedelta(weeks=1)
    journaliser(db, auteur, "emploi_du_temps",
                f"{rapport.modules_crees} module(s), {rapport.seances_creees} séance(s) du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}")
    db.commit()
    return rapport


# --- Heures des enseignants (base du paiement) -----------------------------------

@dataclass
class HeuresEnseignant:
    cle: str
    nom: str
    email: str | None = None
    minutes: int = 0
    effectuees: int = 0
    annulees: int = 0
    a_confirmer: int = 0
    prevues: int = 0
    par_module: dict[str, int] = field(default_factory=dict)  # « code · nom » -> minutes faites

    @property
    def heures(self) -> float:
        return round(self.minutes / 60, 2)


def cle_enseignant(module: Module) -> tuple[str, str]:
    """Identifie l'enseignant d'un module : son compte s'il est relié, sinon le nom de l'emploi du temps."""
    if module.enseignant_id:
        return f"u{module.enseignant_id}", module.enseignant.nom_complet
    if module.enseignant_nom:
        return f"n:{module.enseignant_nom}", module.enseignant_nom
    return "x", "Enseignant non renseigné"


def seances_periode(db: Session, debut: date, fin: date, cle: str | None = None,
                    enseignant_id: int | None = None) -> list[Seance]:
    q = select(Seance).join(Module).where(Seance.date >= debut, Seance.date <= fin).order_by(Seance.date, Seance.debut)
    if enseignant_id is not None:
        q = q.where(Module.enseignant_id == enseignant_id)
    seances = list(db.scalars(q))
    if cle is not None:
        seances = [s for s in seances if cle_enseignant(s.module)[0] == cle]
    return seances


def heures_par_enseignant(db: Session, debut: date, fin: date, enseignant_id: int | None = None) -> list[HeuresEnseignant]:
    """Additionne, par enseignant, les séances faites (statut « effectuee ») entre deux dates.
    Les séances passées encore « prévues » sont signalées « à confirmer » : elles ne sont pas payées
    tant que l'administration ne les a pas confirmées (ou clôturé l'appel)."""
    aujourdhui = date.today()
    bilan: dict[str, HeuresEnseignant] = {}
    for s in seances_periode(db, debut, fin, enseignant_id=enseignant_id):
        cle, nom = cle_enseignant(s.module)
        ligne = bilan.setdefault(cle, HeuresEnseignant(cle=cle, nom=nom,
                                 email=s.module.enseignant.email if s.module.enseignant else None))
        statut = s.statut_effectif
        if statut == "effectuee":
            ligne.effectuees += 1
            ligne.minutes += s.minutes_comptees
            module = f"{s.module.code} · {s.module.nom}" if s.module.code else s.module.nom
            ligne.par_module[module] = ligne.par_module.get(module, 0) + s.minutes_comptees
        elif statut == "annulee":
            ligne.annulees += 1
        elif s.date <= aujourdhui:
            ligne.a_confirmer += 1
        else:
            ligne.prevues += 1
    return sorted(bilan.values(), key=lambda h: h.nom.lower())


def changer_statut_seance(db: Session, seance: Seance, statut: str, auteur: User,
                          motif: str = "", duree_minutes: int | None = None) -> None:
    if statut not in ("prevue", "effectuee", "annulee"):
        raise ValueError("statut inconnu")
    ancien = seance.statut_effectif
    seance.statut = statut
    seance.motif = (motif or "").strip()[:200] or None
    if duree_minutes is not None:
        seance.duree_minutes = duree_minutes if duree_minutes != seance.duree_prevue_minutes else None
    journaliser(db, auteur, "heures", f"{seance.module.nom} du {seance.date:%d/%m/%Y} : {ancien} → {statut}"
                + (f", durée {seance.minutes_comptees} min" if statut == "effectuee" else "")
                + (f" ({seance.motif})" if seance.motif else ""))
    db.commit()
