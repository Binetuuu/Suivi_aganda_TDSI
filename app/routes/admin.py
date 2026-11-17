import io
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ROLES, TYPES_SEANCE, Journal, Module, Presence, Seance, User, condition_etudiant, format_duree
from ..security import masquer_telephone
from ..services import (
    COLONNES_IMPORT, EMPLOI_DU_TEMPS_L3TDSI_S2, RapportImport, classes, creer_ou_maj_utilisateur, envoyer_invitation,
    importer_emploi_du_temps, importer_utilisateurs, preparer_lien,
    journaliser, lire_fichier_import, seances_sans_cahier, stats_etudiant, taux_global,
    changer_statut_seance, cle_enseignant, heures_par_enseignant, modele_import_xlsx, seances_periode,
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
        inscrits = db.scalar(select(func.count()).select_from(User).where(condition_etudiant(), User.classe == s.module.classe, User.actif.is_(True))) or 0
        presents = sum(1 for p in s.presences if p.statut == "present")
        en_retard = s.appel_statut == "non_lance" and datetime.combine(aujourdhui, s.debut) + timedelta(minutes=15) < datetime.now()
        live.append({"seance": s, "presents": presents, "inscrits": inscrits, "en_retard": en_retard})
    etudiants = list(db.scalars(select(User).where(condition_etudiant(), User.actif.is_(True))))
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


@router.get("/modele-import.xlsx")
def modele_import_excel(user: User = Depends(admin_requis)):
    return Response(modele_import_xlsx(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=Import_comptes_TDSI_Campus.xlsx"})


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


@router.post("/mon-profil-etudiant")
def mon_profil_etudiant(request: Request, matricule: str = Form(""), classe: str = Form(""),
                        user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    matricule, classe = matricule.strip().upper() or None, classe.strip() or None
    if bool(matricule) != bool(classe):
        flash(request, "Indiquez à la fois votre matricule et votre classe (ou videz les deux).", "erreur")
        return rediriger("/admin/utilisateurs")
    if matricule and db.scalars(select(User).where(User.matricule == matricule, User.id != user.id)).first():
        flash(request, "Ce matricule est déjà utilisé par un autre compte.", "erreur")
        return rediriger("/admin/utilisateurs")
    user.matricule, user.classe = matricule, classe
    journaliser(db, user, "profil_etudiant", f"{user.nom_complet} : {matricule or 'profil étudiant retiré'}")
    db.commit()
    flash(request, "Profil étudiant enregistré : choisissez « Étudiant » à la connexion, ou utilisez le lien du menu."
          if matricule else "Profil étudiant retiré.")
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


# --- Heures des enseignants ----------------------------------------------------------

def _periode(du: date | None, au: date | None) -> tuple[date, date]:
    aujourdhui = date.today()
    debut = du or aujourdhui.replace(day=1)
    fin = au or (debut.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return (debut, fin) if debut <= fin else (fin, debut)


def _raccourcis_periode() -> list[tuple[str, date, date]]:
    aujourdhui = date.today()
    debut_mois = aujourdhui.replace(day=1)
    fin_mois = (debut_mois.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    fin_mois_dernier = debut_mois - timedelta(days=1)
    return [("Ce mois-ci", debut_mois, fin_mois), ("Mois dernier", fin_mois_dernier.replace(day=1), fin_mois_dernier)]


@router.get("/heures")
def heures(request: Request, du: date | None = None, au: date | None = None,
           user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    debut, fin = _periode(du, au)
    lignes = heures_par_enseignant(db, debut, fin)
    return rendre(request, "admin_heures.html", user=user, lignes=lignes, debut=debut, fin=fin,
                  total=sum(l.minutes for l in lignes), a_confirmer=sum(l.a_confirmer for l in lignes),
                  raccourcis=_raccourcis_periode(), format_duree=format_duree)


@router.get("/heures/detail")
def heures_detail(request: Request, cle: str, du: date | None = None, au: date | None = None,
                  user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    debut, fin = _periode(du, au)
    seances = seances_periode(db, debut, fin, cle=cle)
    lignes = [l for l in heures_par_enseignant(db, debut, fin) if l.cle == cle]
    return rendre(request, "heures_detail.html", user=user, seances=seances, bilan=lignes[0] if lignes else None,
                  debut=debut, fin=fin, cle=cle, format_duree=format_duree, aujourdhui=date.today(), admin=True)


@router.post("/seances/{seance_id}/statut")
def statut_seance(
    request: Request, seance_id: int, statut: str = Form(...), motif: str = Form(""),
    duree: str = Form(""), retour: str = Form("/admin/heures"),
    user: User = Depends(admin_requis), db: Session = Depends(get_db),
):
    seance = db.get(Seance, seance_id)
    if seance is None:
        raise HTTPException(404, "Séance introuvable.")
    duree_minutes = None
    if duree.strip():
        try:
            h, _, m = duree.strip().replace("h", ":").partition(":")
            duree_minutes = int(h or 0) * 60 + int(m or 0)
        except ValueError:
            duree_minutes = -1
        if not 0 < duree_minutes <= 12 * 60:
            flash(request, "Durée invalide : écrivez par exemple 2:30 pour 2 h 30.", "erreur")
            return rediriger(retour if retour.startswith("/") else "/admin/heures")
    try:
        changer_statut_seance(db, seance, statut, user, motif, duree_minutes)
    except ValueError:
        raise HTTPException(400, "Statut inconnu.")
    libelle = {"effectuee": "confirmée (heures comptées)", "annulee": "annulée (non comptée)", "prevue": "remise à confirmer"}[statut]
    flash(request, f"{seance.module.nom} du {seance.date:%d/%m} : séance {libelle}.")
    return rediriger(retour if retour.startswith("/") and not retour.startswith("//") else "/admin/heures")


@router.get("/heures/export.xlsx")
def export_heures(du: date | None = None, au: date | None = None,
                  user: User = Depends(admin_requis), db: Session = Depends(get_db)):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    debut, fin = _periode(du, au)
    lignes = heures_par_enseignant(db, debut, fin)
    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"
    ws.append([f"Heures d'enseignement du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    entetes = ["Enseignant", "Séances faites", "Heures faites", "Durée (h min)", "Séances annulées", "À confirmer"]
    ws.append(entetes)
    for l in lignes:
        ws.append([l.nom, l.effectuees, l.heures, format_duree(l.minutes), l.annulees, l.a_confirmer])
    ws.append(["TOTAL", sum(l.effectuees for l in lignes), round(sum(l.minutes for l in lignes) / 60, 2),
               format_duree(sum(l.minutes for l in lignes)), sum(l.annulees for l in lignes), sum(l.a_confirmer for l in lignes)])
    for cellule in ws[3]:
        cellule.font = Font(bold=True, color="FFFFFF")
        cellule.fill = PatternFill("solid", fgColor="2557D6")
    for cellule in ws[ws.max_row]:
        cellule.font = Font(bold=True)
    for lettre, largeur in zip("ABCDEF", [34, 15, 14, 15, 17, 13]):
        ws.column_dimensions[lettre].width = largeur

    detail = wb.create_sheet("Détail des séances")
    detail.append(["Enseignant", "Date", "Horaire", "UE", "Module", "Classe", "Type", "Statut", "Durée comptée (h)", "Appel", "Cahier de texte", "Motif"])
    for cellule in detail[1]:
        cellule.font = Font(bold=True, color="FFFFFF")
        cellule.fill = PatternFill("solid", fgColor="2557D6")
    libelles = {"effectuee": "Faite", "annulee": "Annulée", "prevue": "À confirmer"}
    for s in seances_periode(db, debut, fin):
        statut = s.statut_effectif
        detail.append([
            cle_enseignant(s.module)[1], s.date, s.horaire, s.module.code or "", s.module.nom, s.module.classe, s.type,
            libelles[statut] if statut != "prevue" or s.date <= date.today() else "Prévue",
            round(s.minutes_comptees / 60, 2) if statut == "effectuee" else 0,
            "Clôturé" if s.appel_statut == "clos" else ("Ouvert" if s.appel_statut == "ouvert" else "Non fait"),
            "Rempli" if s.cahier else "Non rempli", s.motif or "",
        ])
        detail.cell(row=detail.max_row, column=2).number_format = "DD/MM/YYYY"
    for lettre, largeur in zip("ABCDEFGHIJKL", [30, 12, 15, 20, 40, 10, 7, 13, 17, 10, 15, 30]):
        detail.column_dimensions[lettre].width = largeur
    tampon = io.BytesIO()
    wb.save(tampon)
    nom = f"heures_enseignants_{debut:%Y-%m-%d}_{fin:%Y-%m-%d}.xlsx"
    return Response(tampon.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})
