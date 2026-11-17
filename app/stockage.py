"""Stockage des supports de cours.

- "local" : dossier UPLOAD_DIR (ordinateur, serveur avec disque permanent).
- "db"    : dans la base de données (Render gratuit : le disque s'efface à chaque redémarrage).
- "gcs"   : bucket Cloud Storage / Firebase Storage (TDSI_GCS_BUCKET).
"""
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _bucket():
    from google.cloud import storage  # importé seulement si un bucket est configuré

    return storage.Client().bucket(config.GCS_BUCKET)


def enregistrer(nom: str, contenu: bytes, type_mime: str | None = None) -> None:
    type_mime = type_mime or "application/octet-stream"
    if config.STOCKAGE == "gcs":
        _bucket().blob(f"supports/{nom}").upload_from_string(contenu, content_type=type_mime)
    elif config.STOCKAGE == "db":
        from .db import SessionLocal
        from .models import Fichier

        with SessionLocal() as db:
            db.merge(Fichier(nom=nom, type_mime=type_mime, contenu=contenu))
            db.commit()
    else:
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (config.UPLOAD_DIR / nom).write_bytes(contenu)


def lire(nom: str) -> bytes | None:
    if config.STOCKAGE == "gcs":
        blob = _bucket().blob(f"supports/{nom}")
        return blob.download_as_bytes() if blob.exists() else None
    if config.STOCKAGE == "db":
        from .db import SessionLocal
        from .models import Fichier

        with SessionLocal() as db:
            fichier = db.get(Fichier, nom)
            return fichier.contenu if fichier else None
    chemin = config.UPLOAD_DIR / nom
    return chemin.read_bytes() if chemin.is_file() else None
