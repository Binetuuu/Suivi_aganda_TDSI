from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Colonnes ajoutées après la première mise en ligne : create_all() crée les tables
# manquantes mais ne modifie pas une table existante, on ajoute donc ces colonnes ici.
_COLONNES_AJOUTEES = {
    "users": {"invitation_nonce": "VARCHAR(32)"},
    "modules": {"code": "VARCHAR(40)", "enseignant_nom": "VARCHAR(120)"},
    "seances": {"statut": "VARCHAR(12) DEFAULT 'prevue'", "duree_minutes": "INTEGER", "motif": "VARCHAR(200)",
                "emargement_signature": "TEXT", "emargement_nom": "VARCHAR(120)", "emargement_heure": "TIMESTAMP"},
}


def _migrer() -> None:
    inspecteur = inspect(engine)
    tables = set(inspecteur.get_table_names())
    with engine.begin() as connexion:
        for table, colonnes in _COLONNES_AJOUTEES.items():
            if table not in tables:
                continue
            existantes = {c["name"] for c in inspecteur.get_columns(table)}
            for nom, type_sql in colonnes.items():
                if nom not in existantes:
                    connexion.execute(text(f"ALTER TABLE {table} ADD COLUMN {nom} {type_sql}"))
                    if (table, nom) == ("seances", "statut"):
                        # Les séances dont l'appel était déjà clôturé ont bien eu lieu.
                        connexion.execute(text("UPDATE seances SET statut = 'effectuee' WHERE appel_statut = 'clos'"))


def init_db() -> None:
    from . import models  # noqa: F401  (enregistre les tables)

    Base.metadata.create_all(engine)
    _migrer()
