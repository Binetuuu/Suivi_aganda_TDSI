from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import OtpCode, User
from ..notifications import envoyer_email
from ..security import generer_otp, hash_otp, hash_password, masquer_email, verify_password
from ..services import envoyer_invitation, journaliser, lire_lien, preparer_lien, trouver_par_identifiant
from ..web import flash, rediriger, rendre, utilisateur_courant

router = APIRouter()

ACCUEIL = {"etudiant": "/etudiant", "enseignant": "/enseignant", "admin": "/admin"}
MAX_ESSAIS_OTP = 5


def _connecter(request: Request, db: Session, user: User, message: str | None = None):
    request.session.clear()
    request.session["uid"] = user.id
    if message:
        flash(request, message)
    user.derniere_connexion = datetime.now()
    journaliser(db, user, "connexion", f"Connexion de {user.nom_complet}")
    db.commit()
    return rediriger("/mot-de-passe" if user.must_change_password else ACCUEIL[user.role])


@router.get("/")
def accueil(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is not None and user.actif:
        return rediriger(ACCUEIL[user.role])
    return rendre(request, "login.html")  # comme le site modèle : l'accueil est la carte de connexion


@router.get("/login")
def page_login(request: Request):
    return rendre(request, "login.html")


@router.post("/login")
def login(request: Request, identifiant: str = Form(...), mot_de_passe: str = Form(...), db: Session = Depends(get_db)):
    user = trouver_par_identifiant(db, identifiant)
    if user is not None and user.actif and not user.compte_active:
        return rendre(request, "login.html", identifiant=identifiant,
                      erreur="Ce compte n'est pas encore activé : ouvrez le lien reçu par e-mail pour choisir votre mot de passe.")
    if user is None or not user.actif or not verify_password(mot_de_passe, user.password_hash):
        journaliser(db, user, "echec_connexion", f"Identifiant saisi : {identifiant[:60]}")
        db.commit()
        return rendre(request, "login.html", erreur="Identifiant ou mot de passe incorrect.", identifiant=identifiant)

    if not config.OTP_ENABLED or not user.email:
        return _connecter(request, db, user)

    code = generer_otp()
    db.add(OtpCode(user_id=user.id, code_hash=hash_otp(code),
                   expires_at=datetime.now() + timedelta(minutes=config.OTP_DUREE_MINUTES)))
    db.commit()
    texte = f"Votre code de connexion TDSI est {code}. Il expire dans {config.OTP_DUREE_MINUTES} minutes."
    envoyer_email(user.email, "Code de connexion TDSI", texte)
    request.session.clear()
    request.session["otp_uid"] = user.id
    request.session["otp_email"] = masquer_email(user.email)
    return rediriger("/login/code")


@router.get("/login/code")
def page_code(request: Request):
    if "otp_uid" not in request.session:
        return rediriger("/login")
    return rendre(request, "code.html", email=request.session.get("otp_email", ""))


@router.post("/login/code")
def verifier_code(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    uid = request.session.get("otp_uid")
    if not uid:
        return rediriger("/login")
    otp = db.scalars(
        select(OtpCode).where(OtpCode.user_id == uid, OtpCode.utilise.is_(False)).order_by(OtpCode.id.desc())
    ).first()
    if otp is None or otp.expires_at < datetime.now() or otp.essais >= MAX_ESSAIS_OTP:
        request.session.clear()
        flash(request, "Code expiré ou trop d'essais. Reconnectez-vous.", "erreur")
        return rediriger("/login")
    if otp.code_hash != hash_otp(code.strip()):
        otp.essais += 1
        db.commit()
        return rendre(request, "code.html", email=request.session.get("otp_email", ""), erreur="Code incorrect.")
    otp.utilise = True
    user = db.get(User, uid)
    return _connecter(request, db, user)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return rediriger("/login")


@router.get("/mot-de-passe")
def page_mdp(request: Request, user: User = Depends(utilisateur_courant)):
    return rendre(request, "mot_de_passe.html", user=user)


@router.post("/mot-de-passe")
def changer_mdp(
    request: Request,
    actuel: str = Form(...),
    nouveau: str = Form(...),
    confirmation: str = Form(...),
    user: User = Depends(utilisateur_courant),
    db: Session = Depends(get_db),
):
    erreur = None
    if not verify_password(actuel, user.password_hash):
        erreur = "Le mot de passe actuel est incorrect."
    elif len(nouveau) < 8:
        erreur = "Le nouveau mot de passe doit contenir au moins 8 caractères."
    elif nouveau != confirmation:
        erreur = "Les deux mots de passe ne correspondent pas."
    elif nouveau == actuel:
        erreur = "Choisissez un mot de passe différent du mot de passe provisoire."
    if erreur:
        return rendre(request, "mot_de_passe.html", user=user, erreur=erreur)
    user = db.merge(user)
    user.password_hash = hash_password(nouveau)
    user.must_change_password = False
    journaliser(db, user, "mot_de_passe", "Mot de passe modifié")
    db.commit()
    flash(request, "Mot de passe enregistré.")
    return rediriger(ACCUEIL[user.role])


# --- Inscription par lien et mot de passe oublié -----------------------------------

def _verifier_nouveau_mdp(nouveau: str, confirmation: str) -> str | None:
    if len(nouveau) < 8:
        return "Le mot de passe doit contenir au moins 8 caractères."
    if nouveau != confirmation:
        return "Les deux mots de passe ne correspondent pas."
    return None


@router.get("/inscription/{jeton}")
def page_inscription(request: Request, jeton: str, db: Session = Depends(get_db)):
    user = lire_lien(db, jeton)
    if user is None:
        return rendre(request, "inscription.html", invalide=True)
    return rendre(request, "inscription.html", personne=user, jeton=jeton)


@router.post("/inscription/{jeton}")
def inscription(
    request: Request, jeton: str, nouveau: str = Form(...), confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    user = lire_lien(db, jeton)
    if user is None:
        return rendre(request, "inscription.html", invalide=True)
    erreur = _verifier_nouveau_mdp(nouveau, confirmation)
    if erreur:
        return rendre(request, "inscription.html", personne=user, jeton=jeton, erreur=erreur)
    premiere_fois = not user.compte_active
    user.password_hash = hash_password(nouveau)
    user.must_change_password = False
    user.invitation_nonce = None  # le lien ne peut plus resservir
    journaliser(db, user, "inscription" if premiere_fois else "mot_de_passe_reinitialise", user.nom_complet)
    db.commit()
    # La personne vient de prouver qu'elle reçoit les e-mails de ce compte : connexion directe.
    return _connecter(request, db, user, "Bienvenue ! Votre compte est activé." if premiere_fois else "Nouveau mot de passe enregistré.")


def _envoyer_lien(request: Request, identifiant: str, db: Session, mode: str):
    user = trouver_par_identifiant(db, identifiant)
    if user is not None and user.actif and user.email:
        preparer_lien(user)
        journaliser(db, user, "demande_activation" if mode == "activer" else "demande_reinitialisation", user.nom_complet)
        db.commit()
        envoyer_invitation(user)
    # Même réponse dans tous les cas : on ne révèle pas quels comptes existent.
    return rendre(request, "oubli.html", envoye=True, mode=mode)


@router.get("/mot-de-passe-oublie")
def page_oubli(request: Request):
    return rendre(request, "oubli.html", mode="oubli")


@router.post("/mot-de-passe-oublie")
def oubli(request: Request, identifiant: str = Form(...), db: Session = Depends(get_db)):
    return _envoyer_lien(request, identifiant, db, "oubli")


@router.get("/activer")
def page_activer(request: Request):
    return rendre(request, "oubli.html", mode="activer")


@router.post("/activer")
def activer(request: Request, identifiant: str = Form(...), db: Session = Depends(get_db)):
    return _envoyer_lien(request, identifiant, db, "activer")
