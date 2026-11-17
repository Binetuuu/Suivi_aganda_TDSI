"""Initialisation de la base.

    python -m app.seed --admin-email admin@exemple.sn      crée le premier compte administrateur
    python -m app.seed --demo                              ajoute des données de démonstration fictives
"""
import argparse
from datetime import date, time, timedelta

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import MOT_DE_PASSE_NON_DEFINI, Module, Seance, User
from .security import hash_password, normaliser_email, normaliser_telephone
from .services import debut_semaine, envoyer_invitation, lien_inscription, preparer_lien


def creer_admin(email: str | None, telephone: str | None, prenom: str, nom: str) -> None:
    with SessionLocal() as db:
        email, telephone = normaliser_email(email), normaliser_telephone(telephone)
        if not email:
            raise SystemExit("Indiquez une adresse e-mail valide pour l'administrateur (--admin-email).")
        existant = db.scalars(select(User).where(User.email == email)).first()
        if existant:
            raise SystemExit("Un compte existe déjà avec ces coordonnées.")
        admin = User(nom=nom, prenom=prenom, role="admin", email=email, telephone=telephone,
                     password_hash=MOT_DE_PASSE_NON_DEFINI, must_change_password=False)
        preparer_lien(admin)
        db.add(admin)
        db.commit()
        lien = lien_inscription(admin)
        envoyer_invitation(admin)
    print(f"Administrateur créé ({email}). Ouvrez ce lien pour choisir votre mot de passe :")
    print(f"  {lien}")
    print("Le même lien vient d'être envoyé par e-mail (si l'envoi est configuré).")


def demo() -> None:
    """Données fictives pour essayer l'application (aucune personne réelle)."""
    mdp = "demo-tdsi-2026"
    with SessionLocal() as db:
        if db.scalars(select(User).where(User.email == "admin@demo.tdsi")).first():
            print("Les données de démonstration existent déjà.")
            return

        def compte(**kw):
            u = User(password_hash=hash_password(mdp), must_change_password=False, **kw)
            db.add(u)
            return u

        compte(nom="Démo", prenom="Admin", role="admin", email="admin@demo.tdsi")
        profs = [
            compte(nom="Prof", prenom="Alpha", role="enseignant", email="prof.alpha@demo.tdsi"),
            compte(nom="Prof", prenom="Bêta", role="enseignant", email="prof.beta@demo.tdsi"),
        ]
        for i in range(1, 9):
            compte(nom=f"Étudiant{i}", prenom="Démo", role="etudiant", classe="L3 TDSI", matricule=f"DEMO-L3-{i:04d}",
                   email=f"etudiant{i}@demo.tdsi", delegue=(i == 1))
        db.flush()
        modules = [
            Module(nom="Cryptographie appliquée", classe="L3 TDSI", enseignant_id=profs[0].id),
            Module(nom="Réseaux et protocoles", classe="L3 TDSI", enseignant_id=profs[1].id),
            Module(nom="Sécurité des systèmes", classe="L3 TDSI", enseignant_id=profs[0].id),
        ]
        db.add_all(modules)
        db.flush()
        lundi = debut_semaine(date.today())
        creneaux = [(0, time(8), time(10), 0, "CM"), (0, time(10, 15), time(12, 15), 1, "TP"), (2, time(9), time(11), 2, "TD"),
                    (3, time(8), time(10), 2, "CM"), (4, time(10), time(12), 0, "TD")]
        for semaine in range(-1, 3):
            for jour, debut, fin, m, t in creneaux:
                db.add(Seance(module_id=modules[m].id, date=lundi + timedelta(days=jour, weeks=semaine),
                              debut=debut, fin=fin, salle="B12", type=t))
        # Une séance aujourd'hui pour tester l'appel tout de suite.
        db.add(Seance(module_id=modules[0].id, date=date.today(), debut=time(0, 0), fin=time(23, 59), salle="B12", type="CM"))
        db.commit()
    print("Données de démonstration créées (mot de passe commun : demo-tdsi-2026) :")
    print("  admin@demo.tdsi · prof.alpha@demo.tdsi · DEMO-L3-0001 … DEMO-L3-0008")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-email")
    parser.add_argument("--admin-telephone")
    parser.add_argument("--admin-prenom", default="Admin")
    parser.add_argument("--admin-nom", default="TDSI")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--test-email", metavar="ADRESSE", help="envoie un e-mail de test pour vérifier la configuration SMTP")
    args = parser.parse_args()
    if args.test_email:
        from . import config
        from .notifications import envoyer_email

        ok = envoyer_email(args.test_email, "Test TDSI Campus", "Si vous lisez ce message, l'envoi d'e-mails fonctionne.")
        if config.NOTIF_BACKEND not in ("smtp", "brevo"):
            print("TDSI_NOTIF_BACKEND vaut « console » : le message a seulement été affiché ci-dessus.")
        else:
            print("E-mail envoyé." if ok else "Échec de l'envoi : vérifiez la clé Brevo ou les réglages SMTP.")
        return
    init_db()
    if args.admin_email:
        creer_admin(args.admin_email, args.admin_telephone, args.admin_prenom, args.admin_nom)
    if args.demo:
        demo()
    if not (args.admin_email or args.demo):
        parser.print_help()


if __name__ == "__main__":
    main()
