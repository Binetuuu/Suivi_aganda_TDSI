import re
from datetime import date, time as heure

from fastapi.testclient import TestClient

from app.main import app
from app.models import CahierEntry, Module, Presence, Seance, User
from app.notifications import BOITE_ENVOI
from app.security import lire_qr_token, qr_token
from app.services import lien_inscription, stats_etudiant

from .conftest import connecter, creer


def _classe(db):
    prof = creer(db, nom="Prof", prenom="Alpha", role="enseignant", email="prof@test.sn")
    admin = creer(db, nom="Admin", prenom="Test", role="admin", email="admin@test.sn")
    e1 = creer(db, nom="Un", prenom="Etu", role="etudiant", classe="L3", matricule="T-0001", telephone="+221770000001",
               email="e1@test.sn", delegue=True)
    e2 = creer(db, nom="Deux", prenom="Etu", role="etudiant", classe="L3", matricule="T-0002", email="e2@test.sn")
    autre = creer(db, nom="Autre", prenom="Etu", role="etudiant", classe="L2", matricule="T-0003", email="e3@test.sn")
    module = Module(nom="Crypto", classe="L3", enseignant_id=prof.id)
    db.add(module)
    db.flush()
    seance = Seance(module_id=module.id, date=date.today(), debut=heure(8), fin=heure(10), salle="B12", type="CM")
    db.add(seance)
    db.commit()
    return prof, admin, e1, e2, autre, module, seance


def test_connexion_par_telephone_avec_code_par_email(client, db):
    creer(db, nom="Ndiaye", prenom="Awa", role="etudiant", classe="L3", matricule="T-0009",
          telephone="+221771234567", email="awa@test.sn")
    r = connecter(client, "77 123 45 67")
    assert r.headers["location"] == "/etudiant"
    assert BOITE_ENVOI[-1]["destinataire"] == "awa@test.sn"
    assert client.get("/etudiant").status_code == 200


def test_mauvais_mot_de_passe_et_mauvais_code(client, db):
    creer(db, nom="A", prenom="B", role="etudiant", classe="L3", matricule="T-0010", email="b@test.sn")
    r = client.post("/login", data={"identifiant": "T-0010", "mot_de_passe": "faux"})
    assert "incorrect" in r.text
    client.post("/login", data={"identifiant": "t-0010", "mot_de_passe": "motdepasse-ok"})
    r = client.post("/login/code", data={"code": "000000"}, follow_redirects=False)
    assert r.status_code == 200 and "Code incorrect" in r.text
    assert client.get("/etudiant", follow_redirects=False).headers["location"] == "/login"


def test_premiere_connexion_impose_le_changement_de_mot_de_passe(client, db):
    creer(db, nom="A", prenom="B", role="enseignant", email="p@test.sn", must_change_password=True)
    assert connecter(client, "p@test.sn").headers["location"] == "/mot-de-passe"
    assert client.get("/enseignant", follow_redirects=False).headers["location"] == "/mot-de-passe"
    r = client.post("/mot-de-passe", data={"actuel": "motdepasse-ok", "nouveau": "nouveau-mdp-1", "confirmation": "nouveau-mdp-1"},
                    follow_redirects=False)
    assert r.headers["location"] == "/enseignant"
    assert client.get("/enseignant").status_code == 200


def test_import_csv_cree_les_comptes_et_signale_les_erreurs(client, db):
    creer(db, nom="Admin", prenom="Test", role="admin", email="admin@test.sn")
    connecter(client, "admin@test.sn")
    csv = ("matricule;nom;prénom;rôle;e-mail;téléphone;classe\n"
           "TDSI-L3-0001;Diop;Moussa;étudiant;m.diop@exemple.sn;77 000 00 01;L3 TDSI\n"
           ";Sarr;Ibrahima;professeur;i.sarr@exemple.sn;;\n"
           "TDSI-L3-0002;Sans;Contact;etudiant;;;L3 TDSI\n"
           "TDSI-L3-0003;Fall;Awa;etudiant;m.diop@exemple.sn;;L3 TDSI\n"
           "TDSI-L3-0001;Diop;Moussa Ibra;etudiant;;;L3 TDSI\n")
    BOITE_ENVOI.clear()
    r = client.post("/admin/import", files={"fichier": ("liste.csv", csv.encode(), "text/csv")})
    assert r.status_code == 200
    assert "2 compte(s) créé(s)" in r.text and "1 mis à jour" in r.text and "2 erreur(s)" in r.text
    assert "déjà utilisé par le matricule TDSI-L3-0001" in r.text
    moussa = db.query(User).filter_by(matricule="TDSI-L3-0001").one()
    assert moussa.telephone == "+221770000001"
    prof = db.query(User).filter_by(email="i.sarr@exemple.sn").one()
    assert prof.role == "enseignant" and not prof.compte_active
    assert "e-mail valide obligatoire" in r.text
    # chaque nouveau compte reçoit par e-mail un lien pour choisir son mot de passe
    assert any(m["destinataire"] == "i.sarr@exemple.sn" and "/inscription/" in m["message"] for m in BOITE_ENVOI)
    # la dernière ligne a mis à jour Moussa (même matricule) au lieu de créer un doublon
    assert db.query(User).filter_by(email="m.diop@exemple.sn").count() == 1
    db.expire_all()
    assert db.query(User).filter_by(matricule="TDSI-L3-0001").one().prenom == "Moussa Ibra"


def test_qr_personnel_permanent_signe_et_revocable(db):
    e = creer(db, nom="A", prenom="B", role="etudiant", classe="L3", matricule="T-0011", email="x@test.sn")
    charger = lambda uid: db.get(User, uid)  # noqa: E731
    jeton = qr_token(e)
    assert jeton == qr_token(e)  # le QR ne change pas : il n'expire jamais
    assert lire_qr_token(jeton, charger)[0].id == e.id
    falsifie = jeton[:-4] + ("0000" if not jeton.endswith("0000") else "1111")
    assert lire_qr_token(falsifie, charger)[0] is None
    assert lire_qr_token("n'importe quoi", charger)[0] is None
    # carte perdue : un nouveau secret invalide l'ancienne carte
    e.qr_secret = "nouveau-secret"
    db.commit()
    assert lire_qr_token(jeton, charger)[1].startswith("Carte invalide ou remplacée")


def test_invitation_par_lien_puis_mot_de_passe_oublie(client, db):
    creer(db, nom="Admin", prenom="Test", role="admin", email="admin@test.sn")
    connecter(client, "admin@test.sn")
    BOITE_ENVOI.clear()
    client.post("/admin/utilisateurs/ajouter", data={"prenom": "Awa", "nom": "Ndiaye", "role": "etudiant",
                                                     "matricule": "T-0100", "classe": "L3", "email": "awa@test.sn"})
    lien = re.search(r"https?://\S+/inscription/\S+", BOITE_ENVOI[-1]["message"]).group(0)
    chemin = lien.split("8000", 1)[-1]
    with TestClient(app) as etu:
        # pas encore de mot de passe : la connexion explique quoi faire
        assert "pas encore activé" in etu.post("/login", data={"identifiant": "T-0100", "mot_de_passe": "x"}).text
        assert "Activer mon compte" in etu.get(chemin).text
        assert "ne correspondent pas" in etu.post(chemin, data={"nouveau": "motdepasse-1", "confirmation": "autre"}).text
        r = etu.post(chemin, data={"nouveau": "mon-mdp-perso", "confirmation": "mon-mdp-perso"}, follow_redirects=False)
        assert r.headers["location"] == "/etudiant"
        assert "Bienvenue" in etu.get("/etudiant").text
        # le lien ne sert qu'une fois
        assert "Lien expiré ou déjà utilisé" in etu.get(chemin).text
    with TestClient(app) as etu2:
        assert connecter(etu2, "T-0100", "mon-mdp-perso").headers["location"] == "/etudiant"
        # mot de passe oublié : même réponse pour un compte inconnu, lien envoyé pour un compte réel
        BOITE_ENVOI.clear()
        assert "Vérifiez vos e-mails" in etu2.post("/mot-de-passe-oublie", data={"identifiant": "inconnu@test.sn"}).text
        assert BOITE_ENVOI == []
        etu2.post("/mot-de-passe-oublie", data={"identifiant": "awa@test.sn"})
        assert "Réinitialisation" in BOITE_ENVOI[-1]["sujet"]
    # un lien falsifié est refusé
    assert "Lien expiré" in client.get("/inscription/faux-jeton").text
    awa = db.query(User).filter_by(matricule="T-0100").one()
    assert lien_inscription(awa).startswith("http")


def test_appel_complet_scan_manuel_cloture(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    connecter(client, "admin@test.sn")  # c'est la responsable (admin) qui scanne
    assert client.post(f"/api/appel/{seance.id}/scan", json={"token": qr_token(e1)}).json()["ok"] is False  # appel pas ouvert
    client.post(f"/appel/{seance.id}/ouvrir")
    r = client.post(f"/api/appel/{seance.id}/scan", json={"token": qr_token(e1)}).json()
    assert r["ok"] and "T-0001" in r["message"]
    assert "déjà" in client.post(f"/api/appel/{seance.id}/scan", json={"token": qr_token(e1)}).json()["message"]
    assert client.post(f"/api/appel/{seance.id}/scan", json={"token": qr_token(autre)}).json()["ok"] is False  # autre classe
    assert client.post(f"/api/appel/{seance.id}/manuel", json={"matricule": "t-9999"}).json()["ok"] is False
    etat = client.get(f"/api/appel/{seance.id}/etat").json()
    assert (etat["presents"], etat["total"]) == (1, 2)
    BOITE_ENVOI.clear()
    client.post(f"/appel/{seance.id}/cloturer")
    db.expire_all()
    assert db.query(Presence).filter_by(seance_id=seance.id, etudiant_id=e2.id).one().statut == "absent"
    assert any(m["destinataire"] == "e2@test.sn" and "Absence" in m["sujet"] for m in BOITE_ENVOI)
    assert stats_etudiant(db, db.get(User, e1.id)).taux == 100
    assert stats_etudiant(db, db.get(User, e2.id)).taux == 0
    # rouvrir puis saisie manuelle : l'absent devient présent
    client.post(f"/appel/{seance.id}/rouvrir")
    assert client.post(f"/api/appel/{seance.id}/manuel", json={"matricule": "t-0002"}).json()["ok"]
    db.expire_all()
    assert db.query(Presence).filter_by(seance_id=seance.id, etudiant_id=e2.id).one().statut == "present"


def test_restrictions_de_roles(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    autre_prof = creer(db, nom="Autre", prenom="Prof", role="enseignant", email="prof2@test.sn")
    connecter(client, "T-0001")
    assert client.get("/admin").status_code == 403
    assert client.get(f"/appel/{seance.id}").status_code == 403
    assert client.post(f"/api/appel/{seance.id}/scan", json={"token": "x"}).status_code == 403
    with TestClient(app) as c2:
        connecter(c2, "prof@test.sn")
        assert c2.get(f"/appel/{seance.id}").status_code == 403  # l'appel est réservé à l'administration
        assert c2.get("/admin/utilisateurs").status_code == 403
    assert autre_prof.id


def test_supports_depot_et_telechargement(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    connecter(client, "prof@test.sn")
    r = client.post("/cours/deposer", data={"module_id": module.id, "titre": "Chapitre 1"},
                    files={"fichier": ("cours.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert "Support publié" in r.text
    r = client.post("/cours/deposer", data={"module_id": module.id, "titre": "Virus"},
                    files={"fichier": ("x.exe", b"MZ", "application/octet-stream")})
    assert "non accepté" in r.text
    support_id = int(re.search(r"/cours/(\d+)/telecharger", r.text).group(1))
    with TestClient(app) as etu:
        connecter(etu, "T-0002")
        assert etu.get(f"/cours/{support_id}/telecharger").content == b"%PDF-1.4 test"
    with TestClient(app) as etu_l2:
        connecter(etu_l2, "T-0003")
        assert etu_l2.get(f"/cours/{support_id}/telecharger").status_code == 404


def test_cahier_de_texte_et_validation_par_delegue(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    connecter(client, "prof@test.sn")
    r = client.post(f"/cahier/{seance.id}/remplir", data={"titre": "RSA", "contenu": "Clés", "devoir": "TD 5"})
    assert "RSA" in r.text and "En attente de validation" in r.text
    entree = db.query(CahierEntry).one()
    with TestClient(app) as non_delegue:
        connecter(non_delegue, "T-0002")
        assert non_delegue.post(f"/cahier/entree/{entree.id}/valider").status_code == 403
    with TestClient(app) as delegue:
        connecter(delegue, "T-0001")
        assert "Validé par" in delegue.post(f"/cahier/entree/{entree.id}/valider").text


def test_justificatif_traite_par_admin(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    seance.appel_statut = "clos"
    db.add(Presence(seance_id=seance.id, etudiant_id=e2.id, statut="absent", via="auto"))
    db.commit()
    p = db.query(Presence).one()
    connecter(client, "T-0002")
    assert "Justificatif envoyé" in client.post(f"/etudiant/absences/{p.id}/justifier", data={"motif": "Certificat médical"}).text
    with TestClient(app) as adm:
        connecter(adm, "admin@test.sn")
        assert "Certificat médical" in adm.get("/admin").text
        assert "accepté" in adm.post(f"/admin/justificatifs/{p.id}/accepte").text
    db.expire_all()
    assert stats_etudiant(db, db.get(User, e2.id)).non_justifiees == 0


def test_planning_et_toutes_les_pages_s_affichent(client, db):
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    connecter(client, "admin@test.sn")
    r = client.post("/admin/seances", data={"module_id": module.id, "jour": date.today().isoformat(), "debut": "14:00",
                                            "fin": "16:00", "salle": "A1", "type": "TD", "semaines": 3})
    assert "3 séance(s) ajoutée(s)" in r.text
    for url in ["/admin", "/admin/appels", "/admin/utilisateurs", "/admin/planning", "/admin/journal", "/suivi", f"/suivi/{e1.id}",
                "/cahier", "/cours", "/emploi-du-temps", "/emploi-du-temps?semaine=1&classe=L3", f"/appel/{seance.id}",
                "/admin/modele-import.csv"]:
        assert client.get(url).status_code == 200, url
    with TestClient(app) as etu:
        connecter(etu, "T-0001")
        for url in ["/etudiant", "/etudiant/carte", "/etudiant/absences", "/cours", "/cahier", "/emploi-du-temps"]:
            assert etu.get(url).status_code == 200, url
        carte = etu.get("/etudiant/carte").text
        assert "<svg" in carte and "Etu UN" in carte and "T-0001" in carte
    with TestClient(app) as p:
        connecter(p, "prof@test.sn")
        for url in ["/enseignant", f"/cahier/{seance.id}/remplir", "/suivi", "/emploi-du-temps"]:
            assert p.get(url).status_code == 200, url


def test_page_accueil_publique(client, db):
    r = client.get("/")
    # comme le site modèle : l'accueil est directement la carte de connexion
    assert r.status_code == 200 and "Se connecter" in r.text and "Mot de passe oublié ?" in r.text
    assert r.headers["cache-control"] == "private, no-store"
    assert client.get("/login").status_code == 200


def test_activer_mon_compte_renvoie_le_lien(client, db):
    creer(db, nom="Ndiaye", prenom="Awa", role="etudiant", classe="L3", matricule="T-0200", email="awa2@test.sn",
          password_hash="!")
    BOITE_ENVOI.clear()
    assert "Activer mon compte" in client.get("/activer").text
    r = client.post("/activer", data={"identifiant": "t-0200"})
    assert "Vérifiez vos e-mails" in r.text
    assert BOITE_ENVOI[-1]["destinataire"] == "awa2@test.sn" and "/inscription/" in BOITE_ENVOI[-1]["message"]


def test_supports_ranges_dans_la_base(client, db, monkeypatch):
    from app import config
    from app.models import Fichier

    monkeypatch.setattr(config, "STOCKAGE", "db")
    prof, admin, e1, e2, autre, module, seance = _classe(db)
    connecter(client, "prof@test.sn")
    r = client.post("/cours/deposer", data={"module_id": module.id, "titre": "Chapitre BD"},
                    files={"fichier": ("cours bd.pdf", b"%PDF contenu en base", "application/pdf")})
    assert "Support publié" in r.text
    assert db.query(Fichier).count() == 1
    support_id = int(re.search(r"/cours/(\d+)/telecharger", r.text).group(1))
    reponse = client.get(f"/cours/{support_id}/telecharger")
    assert reponse.content == b"%PDF contenu en base"
    assert "cours%20bd.pdf" in reponse.headers["content-disposition"]


def test_premier_admin_cree_au_demarrage(db, monkeypatch):
    from app import config
    from app.services import creer_premier_admin

    monkeypatch.setattr(config, "ADMIN_EMAIL", "Responsable@Exemple.sn")
    BOITE_ENVOI.clear()
    creer_premier_admin()
    creer_premier_admin()  # ne crée rien la deuxième fois
    admins = db.query(User).filter_by(role="admin").all()
    assert len(admins) == 1 and admins[0].email == "responsable@exemple.sn" and not admins[0].compte_active
    assert len(BOITE_ENVOI) == 1 and "/inscription/" in BOITE_ENVOI[0]["message"]


def test_refus_brevo_journalise_la_raison(monkeypatch, capsys):
    import io
    import urllib.error

    from app import config, notifications

    monkeypatch.setattr(config, "NOTIF_BACKEND", "brevo")
    monkeypatch.setattr(config, "BREVO_API_KEY", "xkeysib-test")

    def refuser(*args, **kwargs):
        corps = io.BytesIO(b'{"code":"unauthorized","message":"We have detected you are using an unrecognised IP address"}')
        raise urllib.error.HTTPError("https://api.brevo.com/v3/smtp/email", 403, "Forbidden", {}, corps)

    monkeypatch.setattr(notifications.urllib.request, "urlopen", refuser)
    assert notifications.envoyer_email("x@test.sn", "Sujet", "Texte") is False
    sortie = capsys.readouterr().out
    assert "HTTP 403" in sortie and "unrecognised IP address" in sortie and "xkeysib-test" not in sortie


def test_import_emploi_du_temps_L3TDSI(client, db):
    creer(db, nom="Admin", prenom="Test", role="admin", email="admin@test.sn")
    prof = creer(db, nom="Dione", prenom="Nafi", role="enseignant", email="nafi@test.sn")
    connecter(client, "admin@test.sn")
    donnees = {"source": "l3tdsi_s2", "debut": "2026-11-16", "fin": "2026-11-29"}  # deux semaines, lundi → dimanche
    r = client.post("/admin/emploi-du-temps", data=donnees)
    assert "8 module(s) créé(s), 16 séance(s) ajoutée(s)" in r.text
    # relancer ne crée pas de doublons
    r = client.post("/admin/emploi-du-temps", data=donnees)
    assert "0 module(s) créé(s), 0 séance(s) ajoutée(s), 16 déjà présente(s)" in r.text
    crypto = db.query(Module).filter_by(code="LTDSI-CI 3611/3622").one()
    assert crypto.enseignant_nom == "Nafi Dione" and crypto.nom_enseignant == "Nafi Dione"
    mardis = db.query(Seance).filter_by(module_id=crypto.id).order_by(Seance.date).all()
    assert [s.date.isoformat() for s in mardis] == ["2026-11-17", "2026-11-24"]
    assert (mardis[0].debut.hour, mardis[0].fin.hour) == (18, 20)
    samedi = db.query(Seance).join(Module).filter(Module.code == "LTDSI-CI 3632").first()
    assert samedi.date.weekday() == 5 and samedi.debut.hour == 10
    # relier le module au compte de l'enseignante
    client.post(f"/admin/modules/{crypto.id}/enseignant", data={"enseignant_id": prof.id})
    db.expire_all()
    assert db.get(Module, crypto.id).nom_enseignant == "Nafi Dione" and db.get(Module, crypto.id).enseignant_id == prof.id
    assert "Programmation orientée objet" in client.get("/admin/planning").text
    assert "Mathématique discrète" in client.get("/admin/emploi-du-temps-L3TDSI-S2.csv").text


def test_import_xlsx_ignore_les_lignes_vides_du_modele(client, db):
    import io

    from openpyxl import Workbook

    creer(db, nom="Admin", prenom="Test", role="admin", email="admin@test.sn")
    connecter(client, "admin@test.sn")
    wb = Workbook()
    ws = wb.active
    ws.append(["matricule", "nom", "prenom", "role", "email", "telephone", "classe"])
    ws.append(["TDSI-L3-0101", "FALL", "Awa", "etudiant", "awa.fall@exemple.sn", "770000001", "L3 TDSI"])
    ws.append(["TDSI-L3-0102", "SY", "Abdou", "etudiant", "abdou.sy@exemple.sn", None, "L3 TDSI"])
    for _ in range(20):  # lignes du modèle non remplies
        ws.append([None, None, None, "etudiant", None, None, "L3 TDSI"])
    tampon = io.BytesIO()
    wb.save(tampon)
    r = client.post("/admin/import", files={"fichier": ("comptes.xlsx", tampon.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert "2 compte(s) créé(s)" in r.text and "0 erreur(s)" in r.text


def test_heures_enseignants_pour_le_paiement(client, db):
    import io
    from datetime import timedelta

    from openpyxl import load_workbook

    prof, admin, e1, e2, autre, module, seance = _classe(db)  # séance du jour 08:00-10:00
    hier = date.today() - timedelta(days=1)
    s_hier = Seance(module_id=module.id, date=hier, debut=heure(14), fin=heure(17), type="TD")
    s_annulee = Seance(module_id=module.id, date=hier, debut=heure(8), fin=heure(10), type="CM")
    autre_module = Module(nom="Réseaux", classe="L3", enseignant_nom="M. Mbaye")  # enseignant sans compte
    db.add_all([s_hier, s_annulee, autre_module])
    db.flush()
    s_mbaye = Seance(module_id=autre_module.id, date=hier, debut=heure(18), fin=heure(21), type="CM")
    db.add(s_mbaye)
    db.commit()
    du, au = (date.today() - timedelta(days=2)).isoformat(), date.today().isoformat()
    connecter(client, "admin@test.sn")

    # 1. clôturer l'appel compte la séance (2 h)
    client.post(f"/appel/{seance.id}/ouvrir")
    client.post(f"/appel/{seance.id}/cloturer")
    db.expire_all()
    assert db.get(Seance, seance.id).statut == "effectuee"

    # 2. confirmation manuelle avec durée réelle 2 h 30 au lieu de 3 h ; 3. annulation avec motif
    client.post(f"/admin/seances/{s_hier.id}/statut", data={"statut": "effectuee", "duree": "2:30"})
    client.post(f"/admin/seances/{s_annulee.id}/statut", data={"statut": "annulee", "motif": "Professeur absent"})
    assert "Durée invalide" in client.post(f"/admin/seances/{s_mbaye.id}/statut",
                                           data={"statut": "effectuee", "duree": "abc"}).text

    page = client.get(f"/admin/heures?du={du}&au={au}").text
    assert "Alpha Prof" in page and "4 h 30" in page   # 2 h + 2 h 30
    assert "M. Mbaye" in page        # séance passée non confirmée → à confirmer, 0 h
    detail = client.get(f"/admin/heures/detail?cle=u{prof.id}&du={du}&au={au}").text
    assert "Professeur absent" in detail and "prévu 3 h" in detail

    # 4. export Excel
    r = client.get(f"/admin/heures/export.xlsx?du={du}&au={au}")
    wb = load_workbook(io.BytesIO(r.content))
    synthese = {row[0]: row for row in wb["Synthèse"].iter_rows(min_row=4, values_only=True)}
    assert synthese["Alpha Prof"][1:3] == (2, 4.5)
    assert synthese["M. Mbaye"][5] == 1           # 1 séance à confirmer
    assert synthese["TOTAL"][2] == 4.5
    assert wb["Détail des séances"].max_row == 5  # en-tête + 4 séances

    # 5. l'enseignant voit ses heures mais ne peut pas les modifier
    with TestClient(app) as p:
        connecter(p, "prof@test.sn")
        mes = p.get(f"/enseignant/heures?du={du}&au={au}").text
        assert "4 h 30" in mes and "Confirmer faite" not in mes
        assert p.post(f"/admin/seances/{s_mbaye.id}/statut", data={"statut": "effectuee"}).status_code == 403
