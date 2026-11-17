"""Mots de passe, codes de connexion et QR codes personnels signés."""
import hashlib
import hmac
import re
import secrets

from .config import INDICATIF, SECRET_KEY

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), expected)


def mot_de_passe_temporaire() -> str:
    return secrets.token_urlsafe(8)


def generer_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def normaliser_telephone(numero: str | None) -> str | None:
    """Ramène un numéro à la forme +221771234567 (indicatif par défaut si absent)."""
    if not numero:
        return None
    brut = re.sub(r"[^\d+]", "", str(numero))
    if brut.startswith("00"):
        brut = "+" + brut[2:]
    if not brut.startswith("+"):
        brut = INDICATIF + brut.lstrip("0")
    return brut if len(brut) >= 8 else None


def normaliser_email(email: str | None) -> str | None:
    if not email:
        return None
    email = str(email).strip().lower()
    return email if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) else None


def masquer_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    nom, domaine = email.split("@", 1)
    return f"{nom[:2]}•••@{domaine}"


def masquer_telephone(numero: str | None) -> str:
    if not numero:
        return "—"
    return numero[:-6] + " ••• •• " + numero[-2:] if len(numero) > 8 else numero


# --- QR code personnel ------------------------------------------------------
# Format : TDSI2.<id étudiant>.<signature>
# Le QR est permanent (il n'expire pas). La signature dépend d'un secret propre à
# chaque étudiant : il est impossible de fabriquer la carte d'un autre. Si une carte
# est perdue ou partagée, l'administration génère un nouveau secret et l'ancienne
# carte cesse aussitôt de fonctionner.

def _signature(user_id: int, qr_secret: str) -> str:
    return hmac.new(qr_secret.encode(), f"carte:{user_id}".encode(), hashlib.sha256).hexdigest()[:24]


def qr_token(user) -> str:
    return f"TDSI2.{user.id}.{_signature(user.id, user.qr_secret)}"


def lire_qr_token(token: str, charger_user):
    """Retourne (user, None) si la carte est valide, sinon (None, raison)."""
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != "TDSI2" or not parts[1].isdigit():
        return None, "Ce QR code n'est pas une carte TDSI."
    user = charger_user(int(parts[1]))
    if user is None or not user.actif or user.role != "etudiant":
        return None, "Carte inconnue ou désactivée."
    if not hmac.compare_digest(parts[2], _signature(user.id, user.qr_secret)):
        return None, "Carte invalide ou remplacée : demandez à l'étudiant sa nouvelle carte."
    return user, None
