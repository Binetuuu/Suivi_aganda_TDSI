"""Envoi des e-mails (codes de connexion, invitations, absences).

- TDSI_NOTIF_BACKEND=console : messages seulement affichés dans le terminal (tests).
- TDSI_NOTIF_BACKEND=brevo   : API HTTPS de Brevo (gratuit, 300 e-mails/jour) ; conseillé
  sur Render, qui bloque souvent les ports SMTP sur l'offre gratuite.
- TDSI_NOTIF_BACKEND=smtp    : serveur SMTP, par exemple Gmail avec un mot de passe d'application.
"""
import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from . import config

log = logging.getLogger("tdsi.notifications")

# Derniers messages « envoyés » (utile en développement et pour les tests).
BOITE_ENVOI: list[dict] = []


def _console(destinataire: str, sujet: str, message: str) -> None:
    BOITE_ENVOI.append({"canal": "email", "destinataire": destinataire, "sujet": sujet, "message": message})
    del BOITE_ENVOI[:-200]
    print(f"[EMAIL -> {destinataire}] {sujet}: {message}", flush=True)


def _envoyer_brevo(adresse: str, sujet: str, message: str) -> bool:
    corps = json.dumps({
        "sender": {"email": config.EMAIL_EXPEDITEUR, "name": config.NOM_EXPEDITEUR},
        "to": [{"email": adresse}],
        "subject": sujet,
        "textContent": message,
    }).encode()
    requete = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email", data=corps, method="POST",
        headers={"api-key": config.BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(requete, timeout=15) as reponse:
        return 200 <= reponse.status < 300


def envoyer_email(adresse: str | None, sujet: str, message: str) -> bool:
    if not adresse:
        return False
    try:
        if config.NOTIF_BACKEND == "brevo" and config.BREVO_API_KEY:
            return _envoyer_brevo(adresse, sujet, message)
        if config.NOTIF_BACKEND != "smtp" or not config.SMTP_HOST:
            _console(adresse, sujet, message)
            return True
        msg = EmailMessage()
        msg["From"] = config.SMTP_FROM or config.SMTP_USER
        msg["To"] = adresse
        msg["Subject"] = sujet
        msg.set_content(message)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception:  # un échec d'envoi ne doit pas bloquer l'application
        log.exception("Échec d'envoi de l'e-mail à %s", adresse)
        return False


def notifier(user, sujet: str, message: str) -> bool:
    """Prévient un utilisateur par e-mail."""
    return envoyer_email(user.email, sujet, message)
