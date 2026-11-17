import io
from datetime import date

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CahierEntry, Module, Presence, Seance, Support, User
from ..security import qr_token
from ..services import journaliser, stats_etudiant
from ..web import exiger_role, flash, rediriger, rendre

router = APIRouter()
etudiant_requis = exiger_role("etudiant")


def _seances_du_jour(db: Session, classe: str, jour: date) -> list[Seance]:
    return list(db.scalars(
        select(Seance).join(Module).where(Module.classe == classe, Seance.date == jour).order_by(Seance.debut)
    ))


@router.get("/etudiant")
def tableau_de_bord(request: Request, user: User = Depends(etudiant_requis), db: Session = Depends(get_db)):
    stats = stats_etudiant(db, user)
    aujourdhui = date.today()
    prochaines = list(db.scalars(
        select(Seance).join(Module).where(Module.classe == user.classe, Seance.date >= aujourdhui)
        .order_by(Seance.date, Seance.debut).limit(5)
    ))
    cahier = list(db.scalars(
        select(CahierEntry).join(Seance).join(Module).where(Module.classe == user.classe)
        .order_by(Seance.date.desc()).limit(3)
    ))
    supports = list(db.scalars(
        select(Support).join(Module).where(Module.classe == user.classe).order_by(Support.created_at.desc()).limit(4)
    ))
    return rendre(request, "etudiant_accueil.html", user=user, stats=stats, prochaines=prochaines,
                  cahier=cahier, supports=supports, aujourdhui=aujourdhui)


def svg_qr(texte: str) -> str:
    image = qrcode.make(texte, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    tampon = io.BytesIO()
    image.save(tampon)
    svg = tampon.getvalue().decode()
    return svg[svg.index("<svg"):]  # sans l'en-tête XML, pour l'insérer dans la page


@router.get("/etudiant/carte")
def carte(request: Request, user: User = Depends(etudiant_requis), db: Session = Depends(get_db)):
    presence = db.execute(
        select(Presence, Seance).join(Seance).where(Presence.etudiant_id == user.id, Seance.date == date.today())
        .order_by(Presence.heure.desc())
    ).first()
    statut = None
    if presence:
        p, s = presence
        statut = f"{'Présent(e)' if p.statut == 'present' else 'Absent(e)'} · {s.module.nom} ({p.heure:%H:%M})"
    return rendre(request, "carte.html", user=user, qr=svg_qr(qr_token(user)), statut=statut)


@router.get("/etudiant/absences")
def absences(request: Request, user: User = Depends(etudiant_requis), db: Session = Depends(get_db)):
    lignes = list(db.scalars(
        select(Presence).join(Seance).where(Presence.etudiant_id == user.id, Presence.statut == "absent")
        .order_by(Seance.date.desc())
    ))
    return rendre(request, "absences.html", user=user, absences=lignes, stats=stats_etudiant(db, user))


@router.post("/etudiant/absences/{presence_id}/justifier")
def justifier(
    request: Request, presence_id: int, motif: str = Form(...),
    user: User = Depends(etudiant_requis), db: Session = Depends(get_db),
):
    presence = db.get(Presence, presence_id)
    if presence is None or presence.etudiant_id != user.id or presence.statut != "absent":
        raise HTTPException(404, "Absence introuvable.")
    if presence.justif_statut == "accepte":
        flash(request, "Cette absence est déjà justifiée.", "erreur")
        return rediriger("/etudiant/absences")
    presence.justificatif = motif.strip()[:2000]
    presence.justif_statut = "en_attente"
    journaliser(db, user, "justificatif", f"{user.nom_complet} : absence du {presence.seance.date:%d/%m} en {presence.seance.module.nom}")
    db.commit()
    flash(request, "Justificatif envoyé à l'administration.")
    return rediriger("/etudiant/absences")
