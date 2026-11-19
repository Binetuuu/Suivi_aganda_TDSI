"""Pages communes à tous les rôles : emploi du temps, cours et supports, cahier de texte."""
import mimetypes
import uuid
from urllib.parse import quote
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, stockage
from ..db import get_db
from ..models import CahierEntry, Module, Seance, Support, User, condition_seance_non_annulee
from ..services import classes, debut_semaine, journaliser
from ..web import JOURS, flash, rediriger, rendre, utilisateur_courant

router = APIRouter()


def modules_visibles(db: Session, user: User, classe: str | None = None):
    q = select(Module).order_by(Module.classe, Module.nom)
    if user.role_actif == "etudiant":
        q = q.where(Module.classe == user.classe)
    elif user.role_actif == "enseignant":
        q = q.where(Module.enseignant_id == user.id)
    elif classe:
        q = q.where(Module.classe == classe)
    return list(db.scalars(q))


@router.get("/emploi-du-temps")
def emploi_du_temps(request: Request, semaine: int = 0, classe: str | None = None,
                    user: User = Depends(utilisateur_courant), db: Session = Depends(get_db)):
    lundi = debut_semaine(date.today()) + timedelta(weeks=semaine)
    ids = [m.id for m in modules_visibles(db, user, classe)]
    seances = list(db.scalars(
        select(Seance).where(Seance.module_id.in_(ids), Seance.date >= lundi, Seance.date < lundi + timedelta(days=6))
        .order_by(Seance.date, Seance.debut)
    )) if ids else []
    jours = [{"nom": JOURS[i], "date": lundi + timedelta(days=i),
              "seances": [s for s in seances if s.date == lundi + timedelta(days=i)]} for i in range(6)]
    return rendre(request, "emploi_du_temps.html", user=user, jours=jours, semaine=semaine, lundi=lundi,
                  classe=classe, classes=classes(db) if user.role_actif == "admin" else [])


# --- Cours et supports ----------------------------------------------------------

@router.get("/cours")
def cours(request: Request, module: int | None = None, user: User = Depends(utilisateur_courant), db: Session = Depends(get_db)):
    modules = modules_visibles(db, user)
    ids = [m.id for m in modules]
    q = select(Support).where(Support.module_id.in_(ids)).order_by(Support.created_at.desc())
    if module in ids:
        q = q.where(Support.module_id == module)
    supports = list(db.scalars(q)) if ids else []
    peut_deposer = user.role_actif in ("enseignant", "admin") and bool(modules)
    return rendre(request, "cours.html", user=user, modules=modules, supports=supports,
                  module_actif=module, peut_deposer=peut_deposer,
                  taille_max=config.TAILLE_MAX_SUPPORT // (1024 * 1024))


@router.post("/cours/deposer")
async def deposer(
    request: Request, module_id: int = Form(...), titre: str = Form(...), fichier: UploadFile = File(...),
    user: User = Depends(utilisateur_courant), db: Session = Depends(get_db),
):
    module = db.get(Module, module_id)
    if user.role_actif not in ("enseignant", "admin") or module is None or (user.role_actif == "enseignant" and module.enseignant_id != user.id):
        raise HTTPException(403, "Vous ne pouvez pas déposer dans ce module.")
    nom = Path(fichier.filename or "fichier").name
    extension = Path(nom).suffix.lower()
    if extension not in config.EXTENSIONS_SUPPORT:
        flash(request, f"Format {extension or 'inconnu'} non accepté.", "erreur")
        return rediriger("/cours")
    contenu = await fichier.read(config.TAILLE_MAX_SUPPORT + 1)
    if len(contenu) > config.TAILLE_MAX_SUPPORT:
        flash(request, f"Fichier trop volumineux ({config.TAILLE_MAX_SUPPORT // (1024 * 1024)} Mo maximum).", "erreur")
        return rediriger("/cours")
    nom_stocke = f"{uuid.uuid4().hex}{extension}"
    stockage.enregistrer(nom_stocke, contenu, fichier.content_type)
    support = Support(module_id=module.id, titre=titre.strip()[:200] or nom, fichier_nom=nom,
                      fichier_chemin=nom_stocke, taille=len(contenu), auteur_id=user.id)
    db.add(support)
    journaliser(db, user, "support", f"« {support.titre} » en {module.nom} ({module.classe})")
    db.commit()
    flash(request, "Support publié. Les étudiants de la classe le voient dès maintenant.")
    return rediriger("/cours")


@router.get("/cours/{support_id}/telecharger")
def telecharger(support_id: int, user: User = Depends(utilisateur_courant), db: Session = Depends(get_db)):
    support = db.get(Support, support_id)
    if support is None or support.module_id not in {m.id for m in modules_visibles(db, user)}:
        raise HTTPException(404, "Support introuvable.")
    contenu = stockage.lire(support.fichier_chemin)
    if contenu is None:
        raise HTTPException(404, "Fichier manquant sur le serveur.")
    support.telechargements += 1
    db.commit()
    type_mime = mimetypes.guess_type(support.fichier_nom)[0] or "application/octet-stream"
    return Response(contenu, media_type=type_mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(support.fichier_nom)}"})


# --- Cahier de texte ------------------------------------------------------------

@router.get("/cahier")
def cahier(request: Request, user: User = Depends(utilisateur_courant), db: Session = Depends(get_db)):
    ids = [m.id for m in modules_visibles(db, user)]
    entrees = list(db.scalars(
        select(CahierEntry).join(Seance).where(Seance.module_id.in_(ids)).order_by(Seance.date.desc(), Seance.debut.desc())
    )) if ids else []
    a_remplir = []
    if user.role_actif in ("enseignant", "admin") and ids:
        a_remplir = list(db.scalars(
            select(Seance).outerjoin(CahierEntry, CahierEntry.seance_id == Seance.id)
            .where(Seance.module_id.in_(ids), CahierEntry.id.is_(None), condition_seance_non_annulee(),
                   Seance.date <= date.today())
            .order_by(Seance.date.desc()).limit(10)
        ))
    return rendre(request, "cahier.html", user=user, entrees=entrees, a_remplir=a_remplir)


@router.post("/cahier/entree/{entree_id}/valider")
def valider(request: Request, entree_id: int, user: User = Depends(utilisateur_courant), db: Session = Depends(get_db)):
    entree = db.get(CahierEntry, entree_id)
    if entree is None:
        raise HTTPException(404, "Séance introuvable.")
    delegue_ok = user.role_actif == "etudiant" and user.delegue and user.classe == entree.seance.module.classe
    if not (delegue_ok or user.role_actif == "admin"):
        raise HTTPException(403, "Seul le délégué de la classe peut valider.")
    entree.valide_par_id = user.id
    journaliser(db, user, "cahier_valide", f"{entree.seance.module.nom} du {entree.seance.date:%d/%m}")
    db.commit()
    flash(request, "Séance validée.")
    return rediriger("/cahier")
