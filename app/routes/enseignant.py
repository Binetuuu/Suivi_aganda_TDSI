from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CahierEntry, Module, Presence, Seance, User, condition_etudiant, format_duree
from ..notifications import notifier
from ..security import lire_qr_token
from ..services import etudiants_de_classe, heures_par_enseignant, journaliser, seances_periode, seances_sans_cahier
from ..web import exiger_role, flash, rediriger, rendre

router = APIRouter()
personnel = exiger_role("enseignant", "admin")
# Le scan des présences est fait par la responsable (administration).
admin_seul = exiger_role("admin")


def seance_geree(db: Session, seance_id: int, user: User) -> Seance:
    seance = db.get(Seance, seance_id)
    if seance is None:
        raise HTTPException(404, "Séance introuvable.")
    if user.role_actif != "admin" and seance.module.enseignant_id != user.id:
        raise HTTPException(403, "Cette séance n'est pas la vôtre.")
    return seance


@router.get("/enseignant")
def accueil(request: Request, user: User = Depends(exiger_role("enseignant")), db: Session = Depends(get_db)):
    aujourdhui = date.today()
    base = select(Seance).join(Module).where(Module.enseignant_id == user.id)
    du_jour = list(db.scalars(base.where(Seance.date == aujourdhui).order_by(Seance.debut)))
    a_venir = list(db.scalars(base.where(Seance.date > aujourdhui).order_by(Seance.date, Seance.debut).limit(8)))
    return rendre(request, "enseignant_accueil.html", user=user, du_jour=du_jour, a_venir=a_venir,
                  sans_cahier=seances_sans_cahier(db, user), aujourdhui=aujourdhui)


# --- Appel ----------------------------------------------------------------------

def _etat_appel(db: Session, seance: Seance) -> dict:
    presences = {p.etudiant_id: p for p in seance.presences}
    etudiants = []
    for e in etudiants_de_classe(db, seance.module.classe):
        p = presences.get(e.id)
        etudiants.append({
            "id": e.id, "nom": e.nom_complet, "matricule": e.matricule, "initiales": e.initiales,
            "statut": p.statut if p else "attente",
            "heure": p.heure.strftime("%H:%M") if p and p.statut == "present" else "",
            "via": p.via if p else "",
        })
    presents = sum(1 for e in etudiants if e["statut"] == "present")
    return {"statut": seance.appel_statut, "etudiants": etudiants, "presents": presents,
            "absents": len(etudiants) - presents, "total": len(etudiants)}


def _marquer_present(db: Session, seance: Seance, etudiant: User, via: str, auteur: User) -> dict:
    if seance.appel_statut != "ouvert":
        return {"ok": False, "message": "L'appel n'est pas ouvert."}
    if etudiant.classe != seance.module.classe:
        return {"ok": False, "message": f"{etudiant.nom_complet} n'est pas inscrit(e) en {seance.module.classe}."}
    presence = db.scalars(select(Presence).where(Presence.seance_id == seance.id, Presence.etudiant_id == etudiant.id)).first()
    if presence and presence.statut == "present":
        return {"ok": False, "message": f"{etudiant.nom_complet} est déjà marqué(e) présent(e)."}
    if presence is None:
        presence = Presence(seance_id=seance.id, etudiant_id=etudiant.id, statut="present")
        db.add(presence)
    presence.statut, presence.via, presence.heure = "present", via, datetime.now()
    if via == "manuel":
        journaliser(db, auteur, "presence_manuelle", f"{etudiant.nom_complet} ({etudiant.matricule}) · {seance.module.nom} du {seance.date:%d/%m}")
    db.commit()
    return {"ok": True, "message": f"{etudiant.nom_complet} · {etudiant.matricule} · présent(e) à {presence.heure:%H:%M}"}


@router.get("/appel/{seance_id}")
def page_appel(request: Request, seance_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    return rendre(request, "appel.html", user=user, seance=seance, etat=_etat_appel(db, seance))


@router.post("/appel/{seance_id}/ouvrir")
def ouvrir(seance_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    seance.appel_statut = "ouvert"
    journaliser(db, user, "appel_ouvert", f"{seance.module.nom} ({seance.module.classe}) du {seance.date:%d/%m}")
    db.commit()
    return rediriger(f"/appel/{seance_id}")


@router.post("/appel/{seance_id}/cloturer")
def cloturer(request: Request, seance_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    deja = {p.etudiant_id for p in seance.presences}
    absents = [e for e in etudiants_de_classe(db, seance.module.classe) if e.id not in deja]
    for e in absents:
        db.add(Presence(seance_id=seance.id, etudiant_id=e.id, statut="absent", via="auto"))
    seance.appel_statut = "clos"
    if seance.statut_effectif != "annulee":
        seance.statut = "effectuee"  # l'appel clôturé prouve que le cours a eu lieu : heures comptées
    total_absents = len(absents) + sum(1 for p in seance.presences if p.statut == "absent")
    journaliser(db, user, "appel_clos", f"{seance.module.nom} ({seance.module.classe}) du {seance.date:%d/%m} : {total_absents} absent(s)")
    db.commit()
    for e in absents:
        notifier(e, "Absence enregistrée", f"TDSI : absence enregistrée en {seance.module.nom} le {seance.date:%d/%m} "
                 f"({seance.horaire}). Vous pouvez déposer un justificatif depuis votre espace.")
    flash(request, f"Appel clôturé : {total_absents} absent(s) enregistré(s).")
    return rediriger(f"/appel/{seance_id}")


@router.post("/appel/{seance_id}/rouvrir")
def rouvrir(seance_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    seance.appel_statut = "ouvert"
    journaliser(db, user, "appel_rouvert", f"{seance.module.nom} du {seance.date:%d/%m}")
    db.commit()
    return rediriger(f"/appel/{seance_id}")


@router.post("/appel/{seance_id}/basculer/{etudiant_id}")
def basculer(request: Request, seance_id: int, etudiant_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    etudiant = db.get(User, etudiant_id)
    if etudiant is None or etudiant.classe != seance.module.classe:
        raise HTTPException(404, "Étudiant introuvable.")
    presence = db.scalars(select(Presence).where(Presence.seance_id == seance.id, Presence.etudiant_id == etudiant.id)).first()
    if presence is None:
        presence = Presence(seance_id=seance.id, etudiant_id=etudiant.id, statut="absent")
        db.add(presence)
        nouveau = "present"
    else:
        nouveau = "absent" if presence.statut == "present" else "present"
    presence.statut, presence.via, presence.heure = nouveau, "manuel", datetime.now()
    journaliser(db, user, "correction_presence", f"{etudiant.nom_complet} → {nouveau} · {seance.module.nom} du {seance.date:%d/%m}")
    db.commit()
    return rediriger(f"/appel/{seance_id}")


class Scan(BaseModel):
    token: str


class Saisie(BaseModel):
    matricule: str


@router.post("/api/appel/{seance_id}/scan")
def api_scan(seance_id: int, scan: Scan, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    etudiant, raison = lire_qr_token(scan.token, lambda uid: db.get(User, uid))
    if etudiant is None:
        journaliser(db, user, "scan_refuse", f"{raison} · {seance.module.nom}")
        db.commit()
        return {"ok": False, "message": raison}
    return _marquer_present(db, seance, etudiant, "qr", user)


@router.post("/api/appel/{seance_id}/manuel")
def api_manuel(seance_id: int, saisie: Saisie, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    etudiant = db.scalars(select(User).where(User.matricule == saisie.matricule.strip().upper(), condition_etudiant())).first()
    if etudiant is None:
        return {"ok": False, "message": "Matricule introuvable."}
    return _marquer_present(db, seance, etudiant, "manuel", user)


@router.get("/api/appel/{seance_id}/etat")
def api_etat(seance_id: int, user: User = Depends(admin_seul), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    return _etat_appel(db, seance)


# --- Cahier de texte ------------------------------------------------------------

@router.get("/cahier/{seance_id}/remplir")
def page_remplir(request: Request, seance_id: int, user: User = Depends(personnel), db: Session = Depends(get_db)):
    seance = seance_geree(db, seance_id, user)
    return rendre(request, "cahier_form.html", user=user, seance=seance, entree=seance.cahier)


@router.post("/cahier/{seance_id}/remplir")
def remplir(
    request: Request, seance_id: int,
    titre: str = Form(...), contenu: str = Form(""), devoir: str = Form(""),
    user: User = Depends(personnel), db: Session = Depends(get_db),
):
    seance = seance_geree(db, seance_id, user)
    if not titre.strip():
        flash(request, "Le titre de la séance est obligatoire.", "erreur")
        return rediriger(f"/cahier/{seance_id}/remplir")
    entree = seance.cahier or CahierEntry(seance_id=seance.id, auteur_id=user.id)
    entree.titre, entree.contenu, entree.devoir = titre.strip()[:200], contenu.strip(), devoir.strip()
    entree.valide_par_id = None  # toute modification doit être revalidée par le délégué
    db.add(entree)
    journaliser(db, user, "cahier", f"{seance.module.nom} du {seance.date:%d/%m} : {entree.titre}")
    db.commit()
    flash(request, "Séance enregistrée dans le cahier de texte.")
    return rediriger("/cahier")


@router.get("/enseignant/heures")
def mes_heures(request: Request, du: date | None = None, au: date | None = None,
               user: User = Depends(exiger_role("enseignant")), db: Session = Depends(get_db)):
    """Consultation seule : l'enseignant voit ses heures comptées, l'administration les valide."""
    debut = du or date.today().replace(day=1)
    fin = au or (debut.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if fin < debut:
        debut, fin = fin, debut
    bilans = heures_par_enseignant(db, debut, fin, enseignant_id=user.id)
    seances = seances_periode(db, debut, fin, enseignant_id=user.id)
    return rendre(request, "heures_detail.html", user=user, seances=seances, bilan=bilans[0] if bilans else None,
                  debut=debut, fin=fin, cle=f"u{user.id}", format_duree=format_duree, aujourdhui=date.today(), admin=False)
