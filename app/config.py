"""Configuration de l'application, lue depuis les variables d'environnement ou le fichier .env."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _secret_key() -> str:
    key = os.environ.get("TDSI_SECRET_KEY")
    if key:
        return key
    if os.environ.get("K_SERVICE") or os.environ.get("RENDER"):  # en ligne (Cloud Run, Render)
        raise RuntimeError("TDSI_SECRET_KEY doit être définie en production (voir DEPLOIEMENT.md).")
    # En développement : une clé aléatoire conservée dans un fichier local (ignoré par git).
    key_file = BASE_DIR / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


SECRET_KEY = _secret_key()
def _database_url() -> str:
    url = os.environ.get("TDSI_DATABASE_URL", f"sqlite:///{BASE_DIR / 'tdsi.db'}")
    # Les hébergeurs Postgres (Neon, Supabase…) donnent « postgresql://… » : on précise le pilote psycopg 3.
    for prefixe in ("postgresql://", "postgres://"):
        if url.startswith(prefixe):
            return "postgresql+psycopg://" + url[len(prefixe):]
    return url


DATABASE_URL = _database_url()
UPLOAD_DIR = Path(os.environ.get("TDSI_UPLOAD_DIR", BASE_DIR / "uploads"))
# Où ranger les supports de cours : "local" (dossier), "db" (dans la base, pour Render
# dont le disque s'efface) ou un bucket Cloud Storage si TDSI_GCS_BUCKET est défini.
GCS_BUCKET = os.environ.get("TDSI_GCS_BUCKET", "")
STOCKAGE = "gcs" if GCS_BUCKET else os.environ.get("TDSI_STOCKAGE", "local")
# Adresse publique du site, utilisée dans les liens envoyés par e-mail.
BASE_URL = (os.environ.get("TDSI_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")
# Durée de validité d'un lien d'inscription ou de réinitialisation.
INVITATION_JOURS = int(os.environ.get("TDSI_INVITATION_JOURS", "7"))
# Confirmation de connexion par un code envoyé par e-mail.
OTP_ENABLED = os.environ.get("TDSI_OTP", "1") == "1"
OTP_DUREE_MINUTES = 10
# "console" affiche les e-mails dans le terminal (développement) ; "smtp" (Gmail…) ou
# "brevo" (API HTTP gratuite, conseillée sur Render) les envoient vraiment.
NOTIF_BACKEND = os.environ.get("TDSI_NOTIF_BACKEND", "console")
SMTP_HOST = os.environ.get("TDSI_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("TDSI_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("TDSI_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("TDSI_SMTP_PASSWORD", "")
# Expéditeur affiché ; vide = l'adresse du compte SMTP (obligatoire avec Gmail).
SMTP_FROM = os.environ.get("TDSI_SMTP_FROM", "")
# Cookie de session réservé au HTTPS en production.
COOKIE_SECURE = os.environ.get("TDSI_COOKIE_SECURE", "1" if os.environ.get("RENDER") else "0") == "1"
# Brevo : clé API et expéditeur (adresse vérifiée dans votre compte Brevo).
BREVO_API_KEY = os.environ.get("TDSI_BREVO_API_KEY", "")
EMAIL_EXPEDITEUR = os.environ.get("TDSI_EMAIL_EXPEDITEUR", "")
NOM_EXPEDITEUR = os.environ.get("TDSI_NOM_EXPEDITEUR", "TDSI Campus")
# Premier compte administrateur créé automatiquement au démarrage s'il n'en existe aucun.
ADMIN_EMAIL = os.environ.get("TDSI_ADMIN_EMAIL", "")
# Seuil (en %) sous lequel un étudiant est signalé.
SEUIL_ALERTE = int(os.environ.get("TDSI_SEUIL_ALERTE", "85"))
# Indicatif téléphonique par défaut pour les numéros saisis sans indicatif.
INDICATIF = os.environ.get("TDSI_INDICATIF", "+221")
# Cloud Run refuse les requêtes de plus de 32 Mo : on garde une marge.
TAILLE_MAX_SUPPORT = int(os.environ.get("TDSI_TAILLE_MAX_MO", "30")) * 1024 * 1024
EXTENSIONS_SUPPORT = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".mp4", ".txt", ".png", ".jpg"}
