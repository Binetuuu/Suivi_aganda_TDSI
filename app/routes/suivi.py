from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Module, Presence, Seance, User, condition_etudiant
from ..services import classes, stats_etudiant
from ..web import exiger_role, rendre

router = APIRouter()
personnel = exiger_role("enseignant", "admin")


def _classes_autorisees(db: Session, user: User) -> list[str]:
    if user.role_actif == "admin":
        return classes(db)
    return sorted(set(db.scalars(select(Module.classe).where(Module.enseignant_id == user.id))))


@router.get("/suivi")
def liste(request: Request, classe: str | None = None, user: User = Depends(personnel), db: Session = Depends(get_db)):
    autorisees = _classes_autorisees(db, user)
    choix = classe if classe in autorisees else (autorisees[0] if autorisees else None)
    etudiants = list(db.scalars(
        select(User).where(condition_etudiant(), User.classe == choix).order_by(User.nom, User.prenom)
    )) if choix else []
    lignes = [(e, stats_etudiant(db, e)) for e in etudiants]
    return rendre(request, "suivi_liste.html", user=user, lignes=lignes, classes=autorisees, classe=choix)


@router.get("/suivi/{etudiant_id}")
def detail(request: Request, etudiant_id: int, user: User = Depends(personnel), db: Session = Depends(get_db)):
    etudiant = db.get(User, etudiant_id)
    if etudiant is None or not etudiant.est_etudiant or etudiant.classe not in _classes_autorisees(db, user):
        raise HTTPException(404, "Étudiant introuvable.")
    absences = list(db.scalars(
        select(Presence).join(Seance).where(Presence.etudiant_id == etudiant.id, Presence.statut == "absent")
        .order_by(Seance.date.desc())
    ))
    return rendre(request, "suivi_detail.html", user=user, etudiant=etudiant,
                  stats=stats_etudiant(db, etudiant), absences=absences)
