import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="tdsi-tests-"))
os.environ["TDSI_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["TDSI_UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["TDSI_SECRET_KEY"] = "cle-de-test"
os.environ["TDSI_OTP"] = "1"
os.environ["TDSI_NOTIF_BACKEND"] = "console"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.notifications import BOITE_ENVOI  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    BOITE_ENVOI.clear()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c


def creer(db, **kw) -> User:
    kw.setdefault("password_hash", hash_password("motdepasse-ok"))
    kw.setdefault("must_change_password", False)
    u = User(**kw)
    db.add(u)
    db.commit()
    return u


def connecter(client, identifiant, mdp="motdepasse-ok", profil=None):
    if profil is None:  # profil naturel du compte
        from app.services import trouver_par_identifiant

        with SessionLocal() as s:
            u = trouver_par_identifiant(s, identifiant)
            profil = "etudiant" if u is not None and u.role == "etudiant" else (u.role if u else "etudiant")
    BOITE_ENVOI.clear()
    r = client.post("/login", data={"identifiant": identifiant, "mot_de_passe": mdp, "profil": profil},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login/code", r.text[:300]
    code = re.search(r"\b(\d{6})\b", BOITE_ENVOI[-1]["message"]).group(1)
    return client.post("/login/code", data={"code": code}, follow_redirects=False)
