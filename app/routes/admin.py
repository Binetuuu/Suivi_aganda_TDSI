import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ROLES, TYPES_SEANCE, Journal, Module, Presence, Seance, User
from ..security import masquer_telephone
from ..services import (
    COLONNES_IMPORT, EMPLOI_DU_TEMPS_L3TDSI_S2, RapportImport, classes, creer_ou_maj_utilisateur, envoyer_invitation,
    importer_emploi_du_temps, importer_utilisateurs, preparer_lien,
    journaliser, lire_fichier_import, seances_sans_cahier, stats_etudiant, taux_global,
)
from ..web import flash, rediriger, rendre, exiger_role

router = APIRouter(prefix="/admin")
admin_requis = exiger_role("admin")


@router.get("")
def vue_ensemble(request: Request, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    aujourdhui = date.today()
    compte = dict(db.execute(select(User.role, func.count()).where(User.actif.is_(True)).group_by(User.role)).all())
    non_actives = db.scalar(select(func.count()).select_from(User).where(User.derniere_connexion.is_(None), User.actif.is_(True))) or 0
    seances = list(db.scalars(select(Seance).where(Seance.date == aujourdhui).order_by(Seance.debut)))
    live = []
    for s in seances:
        inscrits = db.scalar(select(func.count()).select_from(User).where(User.role == "etudiant", User.classe == s.module.classe, User.actif.is_(True))) or 0
        presents = sum(1 for p in s.presences if p.statut == "present")
        en_retard = s.appel_statut == "non_lance" and datetime.combine(aujourdhui, s.debut) + timedelta(minutes=15) < datetime.now()
        live.append({"seance": s, "presents": presents, "inscrits": inscrits, "en_retard": en_retard})
    etudiants = list(db.scalars(select(User).where(User.role == "etudiant", User.actif.is_(True))))
    sous_seuil = [(e, st) for e in etudiants if (st := stats_etudiant(db, e)).alerte]
    justificatifs = list(db.scalars(select(Presence).where(Presence.justif_statut == "en_attente").order_by(Presence.heure.desc())))
    journal = list(db.scalars(select(Journal).order_by(Journal.at.desc()).limit(8)))
    return rendre(
        request, "admin_accueil.html", user=user, compte=compte, non_actives=non_actives, live=live,
        taux_jour=taux_global(db, aujourdhui), taux_semestre=taux_global(db), sous_seuil=sous_seuil,
        sans_cahier=seances_sans_cahier(db)[:8], justificatifs=justificatifs, journal=journal, aujourdhui=aujourdhui,
    )


@router.get("/appels")
def appels(request: Request, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    """Point d'entrée de la responsable pour scanner les cartes QR : séances du jour et à venir."""
    aujourdhui = date.today()
    du_jour = list(db.scalars(select(Seance).where(Seance.date == aujourdhui).order_by(Seance.debut)))
    prochaines = list(db.scalars(
        select(Seance).where(Seance.date > aujourdhui, Seance.date <= aujourdhui + timedelta(days=7))
        .order_by(Seance.date, Seance.debut)
    ))
    return rendre(request, "admin_appels.html", user=user, du_jour=du_jour, prochaines=prochaines, aujourdhui=aujourdhui)


@router.post("/justificatifs/{presence_id}/{decision}")
def decider_justificatif(request: Request, presence_id: int, decision: str, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    presence = db.get(Presence, presence_id)
    if presence is None or decision not in ("accepte", "refuse"):
        raise HTTPException(404, "Justificatif introuvable.")
    presence.justif_statut = decision
    journaliser(db, user, "justificatif_" + decision, f"{presence.etudiant.nom_complet} · {presence.seance.module.nom} du {presence.seance.date:%d/%m}")
    db.commit()
    flash(request, "Justificatif " + ("accepté." if decision == "accepte" else "refusé."))
    return rediriger("/admin")


# --- Utilisateurs ---------------------------------------------------------------

@router.get("/utilisateurs")
def utilisateurs(request: Request, role: str | None = None, classe: str | None = None, q: str | None = None,
                 user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    requete = select(User).order_by(User.role, User.classe, User.nom)
    if role in ROLES:
        requete = requete.where(User.role == role)
    if classe:
        requete = requete.where(User.classe == classe)
    if q:
        motif = f"%{q.strip()}%"
        requete = requete.where(User.nom.ilike(motif) | User.prenom.ilike(motif) | User.matricule.ilike(motif) | User.email.ilike(motif))
    rapport = request.session.pop("rapport_import", None)
    return rendre(request, "admin_utilisateurs.html", user=user, utilisateurs=list(db.scalars(requete)),
                  role=role, classe=classe, q=q or "", classes=classes(db), masquer=masquer_telephone, rapport=rapport)


@router.get("/modele-import.csv")
def modele_import(user: User = Depends(admin_requis)):
    contenu = ";".join(COLONNES_IMPORT) + "\n" + "TDSI-L3-0001;Nom;Prénom;etudiant;prenom.nom@exemple.sn;+221770000000;L3 TDSI\n"
    return Response("﻿" + contenu, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=modele_import_tdsi.csv"})


@router.post("/import")
async def importer(request: Request, fichier: UploadFile = File(...), user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    nom = (fichier.filename or "").lower()
    if not nom.endswith((".csv", ".xlsx")):
        flash(request, "Choisissez un fichier .csv ou .xlsx.", "erreur")
        return rediriger("/admin/utilisateurs")
    contenu = await fichier.read(5 * 1024 * 1024)
    try:
        lignes = lire_fichier_import(nom, contenu)
    except Exception:
        flash(request, "Impossible de lire ce fichier. Utilisez le modèle fourni.", "erreur")
        return rediriger("/admin/utilisateurs")
    rapport = importer_utilisateurs(db, lignes, user)
    request.session["rapport_import"] = {"crees": rapport.crees, "mis_a_jour": rapport.mis_a_jour, "erreurs": rapport.erreurs[:50]}
    return rediriger("/admin/utilisateurs")


@router.post("/utilisateurs/ajouter")
def ajouter(
    request: Request, nom: str = Form(...), prenom: str = Form(...), role: str = Form(...),
    matricule: str = Form(""), email: str = Form(""), telephone: str = Form(""), classe: str = Form(""),
    user: User = Depends(admin_requis), db: Session = Depends(get_db),
):
    rapport = RapportImport()
    data = {"nom": nom, "prenom": prenom, "role": role, "matricule": matricule, "email": email, "telephone": telephone, "classe": classe}
    nouveau = creer_ou_maj_utilisateur(db, data, rapport, 1)
    if rapport.erreurs:
        db.rollback()
        flash(request, rapport.erreurs[0].replace("Ligne 1 : ", ""), "erreur")
        return rediriger("/admin/utilisateurs")
    journaliser(db, user, "utilisateur", f"Ajout ou mise à jour de {prenom} {nom} ({role})")
    db.commit()
    if nouveau:
        envoyer_invitation(nouveau)
    flash(request, "Compte créé : le lien d'inscription a été envoyé par e-mail." if nouveau else "Compte existant mis à jour.")
    return rediriger("/admin/utilisateurs")


def _cible(db: Session, user_id: int) -> User:
    cible = db.get(User, user_id)
    if cible is None:
        raise HTTPException(404, "Utilisateur introuvable.")
    return cible


@router.post("/utilisateurs/{user_id}/renvoyer")
def renvoyer(request: Request, user_id: int, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    cible = _cible(db, user_id)
    if not cible.email:
        flash(request, f"{cible.nom_complet} n'a pas d'adresse e-mail : ajoutez-la d'abord.", "erreur")
        return rediriger("/admin/utilisateurs")
    preparer_lien(cible)  # l'ancien lien cesse de fonctionner
    journaliser(db, user, "invitation_renvoyee", cible.nom_complet)
    db.commit()
    envoyer_invitation(cible)
    flash(request, f"Lien envoyé à {cible.nom_complet} ({cible.email}).")
    return rediriger("/admin/utilisateurs")


@router.post("/utilisateurs/{user_id}/activer")
def activer(request: Request, user_id: int, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    cible = _cible(db, user_id)
    if cible.id == user.id:
        flash(request, "Vous ne pouvez pas désactiver votre propre compte.", "erreur")
        return rediriger("/admin/utilisateurs")
    cible.actif = not cible.actif
    journaliser(db, user, "compte_" + ("active" if cible.actif else "desactive"), cible.nom_complet)
    db.commit()
    return rediriger("/admin/utilisateurs")


@router.post("/utilisateurs/{user_id}/delegue")
def delegue(user_id: int, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    cible = _cible(db, user_id)
    if cible.role == "etudiant":
        cible.delegue = not cible.delegue
        journaliser(db, user, "delegue", f"{cible.nom_complet} : {'nommé' if cible.delegue else 'retiré'}")
        db.commit()
    return rediriger("/admin/utilisateurs")


@router.post("/utilisateurs/{user_id}/nouveau-qr")
def nouveau_qr(request: Request, user_id: int, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    cible = _cible(db, user_id)
    cible.qr_secret = secrets.token_hex(32)
    journaliser(db, user, "qr_regenere", cible.nom_complet)
    db.commit()
    flash(request, f"Nouvelle clé QR pour {cible.nom_complet} : l'ancienne carte ne fonctionne plus.")
    return rediriger("/admin/utilisateurs")


# --- Planning : modules et séances -------------------------------------------------

@router.get("/planning")
def planning(request: Request, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    modules = list(db.scalars(select(Module).order_by(Module.classe, Module.nom)))
    enseignants = list(db.scalars(select(User).where(User.role == "enseignant", User.actif.is_(True)).order_by(User.nom)))
    seances = list(db.scalars(select(Seance).where(Seance.date >= date.today()).order_by(Seance.date, Seance.debut).limit(40)))
    return rendre(request, "admin_planning.html", user=user, modules=modules, enseignants=enseignants,
                  seances=seances, classes=classes(db), types=TYPES_SEANCE, aujourdhui=date.today(),
                  fin_defaut=date.today() + timedelta(weeks=16))


@router.post("/modules")
def creer_module(request: Request, nom: str = Form(...), classe: str = Form(...), enseignant_id: int | None = Form(None),
                 user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    if not nom.strip() or not classe.strip():
        flash(request, "Nom et classe obligatoires.", "erreur")
        return rediriger("/admin/planning")
    module = Module(nom=nom.strip(), classe=classe.strip(), enseignant_id=enseignant_id or None)
    db.add(module)
    journaliser(db, user, "module", f"{module.nom} ({module.classe})")
    db.commit()
    flash(request, "Module créé.")
    return rediriger("/admin/planning")


@router.get("/emploi-du-temps-L3TDSI-S2.csv")
def modele_emploi_du_temps(user: User = Depends(admin_requis)):
    contenu = EMPLOI_DU_TEMPS_L3TDSI_S2.read_text(encoding="utf-8")
    return Response("﻿" + contenu, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=emploi_du_temps_L3TDSI_S2.csv"})


@router.post("/emploi-du-temps")
async def importer_edt(
    request: Request, debut: date = Form(...), fin: date = Form(...), source: str = Form("l3tdsi_s2"),
    fichier: UploadFile | None = File(None), user: User = Depends(admin_requis), db: Session = Depends(get_db),
):
    if source == "fichier":
        if fichier is None or not (fichier.filename or "").lower().endswith((".csv", ".xlsx")):
            flash(request, "Choisissez un fichier .csv ou .xlsx.", "erreur")
            return rediriger("/admin/planning")
        nom, contenu = fichier.filename.lower(), await fichier.read(2 * 1024 * 1024)
    else:
        nom, contenu = "edt.csv", EMPLOI_DU_TEMPS_L3TDSI_S2.read_bytes()
    try:
        lignes = lire_fichier_import(nom, contenu)
    except Exception:
        flash(request, "Impossible de lire ce fichier. Partez du modèle fourni.", "erreur")
        return rediriger("/admin/planning")
    rapport = importer_emploi_du_temps(db, lignes, debut, fin, user)
    message = (f"Emploi du temps importé : {rapport.modules_crees} module(s) créé(s), "
               f"{rapport.seances_creees} séance(s) ajoutée(s)"
               + (f", {rapport.deja_presentes} déjà présente(s)" if rapport.deja_presentes else "") + ".")
    flash(request, message, "erreur" if rapport.erreurs else "ok")
    for erreur in rapport.erreurs[:10]:
        flash(request, erreur, "erreur")
    return rediriger("/admin/planning")


@router.post("/modules/{module_id}/enseignant")
def relier_enseignant(request: Request, module_id: int, enseignant_id: int | None = Form(None),
                      user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    module = db.get(Module, module_id)
    enseignant = db.get(User, enseignant_id) if enseignant_id else None
    if module is None or (enseignant is not None and enseignant.role != "enseignant"):
        raise HTTPException(404, "Module ou enseignant introuvable.")
    module.enseignant_id = enseignant.id if enseignant else None
    journaliser(db, user, "module", f"{module.nom} : enseignant {enseignant.nom_complet if enseignant else 'retiré'}")
    db.commit()
    flash(request, f"{module.nom} : enseignant mis à jour.")
    return rediriger("/admin/planning")


@router.post("/seances")
def creer_seances(
    request: Request, module_id: int = Form(...), jour: date = Form(...), debut: str = Form(...), fin: str = Form(...),
    salle: str = Form(""), type: str = Form("CM"), semaines: int = Form(1),
    user: User = Depends(admin_requis), db: Session = Depends(get_db),
):
    module = db.get(Module, module_id)
    try:
        h_debut, h_fin = datetime.strptime(debut, "%H:%M").time(), datetime.strptime(fin, "%H:%M").time()
    except ValueError:
        h_debut = h_fin = None
    if module is None or h_debut is None or h_fin <= h_debut or type not in TYPES_SEANCE:
        flash(request, "Vérifiez le module, le type et les horaires.", "erreur")
        return rediriger("/admin/planning")
    semaines = max(1, min(semaines, 20))
    for i in range(semaines):
        db.add(Seance(module_id=module.id, date=jour + timedelta(weeks=i), debut=h_debut, fin=h_fin, salle=salle.strip(), type=type))
    journaliser(db, user, "planning", f"{semaines} séance(s) de {module.nom} à partir du {jour:%d/%m}")
    db.commit()
    flash(request, f"{semaines} séance(s) ajoutée(s) à l'emploi du temps.")
    return rediriger("/admin/planning")


@router.post("/seances/{seance_id}/supprimer")
def supprimer_seance(request: Request, seance_id: int, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    seance = db.get(Seance, seance_id)
    if seance is None:
        raise HTTPException(404, "Séance introuvable.")
    if seance.presences or seance.cahier:
        flash(request, "Impossible : cette séance a déjà un appel ou un cahier de texte.", "erreur")
        return rediriger("/admin/planning")
    journaliser(db, user, "planning", f"Suppression de {seance.module.nom} du {seance.date:%d/%m}")
    db.delete(seance)
    db.commit()
    return rediriger("/admin/planning")


@router.get("/journal")
def journal(request: Request, user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    lignes = list(db.scalars(select(Journal).order_by(Journal.at.desc()).limit(300)))
    return rendre(request, "admin_journal.html", user=user, lignes=lignes)
