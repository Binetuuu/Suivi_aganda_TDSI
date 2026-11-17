"""Outils web partagés : utilisateur connecté, contrôle des rôles, gabarits, messages flash."""
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import BASE_DIR, SEUIL_ALERTE
from .db import get_db
from .models import User

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def date_fr(d: date, jour: bool = True) -> str:
    texte = f"{d.day} {MOIS[d.month - 1]} {d.year}"
    return f"{JOURS[d.weekday()]} {texte}" if jour else texte


templates.env.filters["date_fr"] = date_fr
templates.env.filters["date_courte"] = lambda d: f"{d:%d/%m/%Y}"
templates.env.globals["SEUIL_ALERTE"] = SEUIL_ALERTE
templates.env.globals["annee"] = date.today().year
# Numéro de version des fichiers CSS/JS : change à chaque mise à jour, ce qui force les
# navigateurs à recharger les styles au lieu de garder l'ancienne version en cache.
_STATIC = BASE_DIR / "app" / "static"
templates.env.globals["version_static"] = max(int(f.stat().st_mtime) for f in _STATIC.iterdir() if f.is_file())


class Redirection(Exception):
    def __init__(self, url: str):
        self.url = url


def utilisateur_courant(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.actif:
        request.session.pop("uid", None)
        raise Redirection("/login")
    user.espace = request.session.get("espace")
    if user.must_change_password and request.url.path != "/mot-de-passe":
        raise Redirection("/mot-de-passe")
    return user


def exiger_role(*roles: str):
    def dependance(user: User = Depends(utilisateur_courant)) -> User:
        if user.role_actif not in roles:
            raise HTTPException(status_code=403, detail="Accès réservé.")
        return user

    return dependance


def flash(request: Request, message: str, niveau: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"message": message, "niveau": niveau})


def rendre(request: Request, gabarit: str, user: User | None = None, **contexte):
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(
        request, gabarit, {"user": user, "messages": messages, "page": request.url.path, **contexte}
    )


def rediriger(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
