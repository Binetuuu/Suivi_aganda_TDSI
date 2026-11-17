import secrets
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("etudiant", "enseignant", "admin")
ROLE_LIBELLES = {"etudiant": "Étudiant", "enseignant": "Enseignant", "admin": "Administration"}
TYPES_SEANCE = ("CM", "TD", "TP")
# Valeur qui ne correspond à aucun mot de passe : compte invité, pas encore activé.
MOT_DE_PASSE_NON_DEFINI = "!"


def _qr_secret() -> str:
    return secrets.token_hex(32)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    matricule: Mapped[str | None] = mapped_column(String(40), unique=True)
    nom: Mapped[str] = mapped_column(String(80))
    prenom: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(160), unique=True)
    telephone: Mapped[str | None] = mapped_column(String(30), unique=True)
    classe: Mapped[str | None] = mapped_column(String(40), index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    qr_secret: Mapped[str] = mapped_column(String(64), default=_qr_secret)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    delegue: Mapped[bool] = mapped_column(Boolean, default=False)
    derniere_connexion: Mapped[datetime | None] = mapped_column(DateTime)
    # Jeton à usage unique du lien d'inscription / de réinitialisation envoyé par e-mail.
    invitation_nonce: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    @property
    def nom_complet(self) -> str:
        return f"{self.prenom} {self.nom}"

    @property
    def est_etudiant(self) -> bool:
        """Étudiant de la classe. Un compte admin peut l'être aussi (la responsable est étudiante) :
        il lui suffit d'avoir un matricule et une classe."""
        return self.role == "etudiant" or (self.role == "admin" and bool(self.matricule) and bool(self.classe))

    @property
    def double_profil(self) -> bool:
        return self.role == "admin" and self.est_etudiant

    @property
    def role_actif(self) -> str:
        """Espace dans lequel la personne navigue : « etudiant » pour un admin qui s'est connecté
        en tant qu'étudiant(e), sinon son rôle. `espace` est fixé à chaque requête depuis la session."""
        if getattr(self, "espace", None) == "etudiant" and self.double_profil:
            return "etudiant"
        return self.role

    @property
    def compte_active(self) -> bool:
        """Faux tant que la personne n'a pas choisi son mot de passe via le lien reçu."""
        return self.password_hash != MOT_DE_PASSE_NON_DEFINI

    @property
    def initiales(self) -> str:
        return (self.prenom[:1] + self.nom[:1]).upper()

    @property
    def role_libelle(self) -> str:
        return ROLE_LIBELLES.get(self.role, self.role)


class Module(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(120))
    classe: Mapped[str] = mapped_column(String(40), index=True)
    enseignant_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Code de l'UE (ex. « LTDSI-CI 3651 ») et nom de l'enseignant tel qu'écrit dans l'emploi
    # du temps, affiché tant qu'aucun compte enseignant n'est relié au module.
    code: Mapped[str | None] = mapped_column(String(40))
    enseignant_nom: Mapped[str | None] = mapped_column(String(120))

    enseignant: Mapped[User | None] = relationship()

    @property
    def nom_enseignant(self) -> str:
        if self.enseignant:
            return self.enseignant.nom_complet
        return self.enseignant_nom or "Enseignant à définir"


class Seance(Base):
    __tablename__ = "seances"

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    debut: Mapped[time] = mapped_column(Time)
    fin: Mapped[time] = mapped_column(Time)
    salle: Mapped[str] = mapped_column(String(60), default="")
    type: Mapped[str] = mapped_column(String(4), default="CM")
    # non_lance | ouvert | clos
    appel_statut: Mapped[str] = mapped_column(String(12), default="non_lance")
    # Pour le paiement des enseignants : prevue | effectuee | annulee.
    # Une séance devient « effectuee » quand l'appel est clôturé ou que l'administration la confirme.
    statut: Mapped[str | None] = mapped_column(String(12), default="prevue")
    # Durée réellement faite, si elle diffère de l'horaire prévu (retard, fin anticipée).
    duree_minutes: Mapped[int | None] = mapped_column(Integer)
    motif: Mapped[str | None] = mapped_column(String(200))

    module: Mapped[Module] = relationship()
    presences: Mapped[list["Presence"]] = relationship(back_populates="seance", cascade="all, delete-orphan")
    cahier: Mapped["CahierEntry | None"] = relationship(back_populates="seance", uselist=False)

    @property
    def horaire(self) -> str:
        return f"{self.debut:%H:%M} – {self.fin:%H:%M}"

    @property
    def duree_prevue_minutes(self) -> int:
        return (self.fin.hour * 60 + self.fin.minute) - (self.debut.hour * 60 + self.debut.minute)

    @property
    def minutes_comptees(self) -> int:
        """Minutes payables : durée réelle si elle a été corrigée, sinon l'horaire prévu."""
        return self.duree_minutes if self.duree_minutes is not None else self.duree_prevue_minutes

    @property
    def statut_effectif(self) -> str:
        return self.statut or "prevue"


def condition_etudiant():
    """Équivalent SQL de User.est_etudiant."""
    from sqlalchemy import and_, or_

    return or_(User.role == "etudiant",
               and_(User.role == "admin", User.matricule.is_not(None), User.classe.is_not(None)))


def format_duree(minutes: int) -> str:
    heures, reste = divmod(int(minutes), 60)
    return f"{heures} h {reste:02d}" if reste else f"{heures} h"


class Presence(Base):
    __tablename__ = "presences"
    __table_args__ = (UniqueConstraint("seance_id", "etudiant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    seance_id: Mapped[int] = mapped_column(ForeignKey("seances.id"), index=True)
    etudiant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # present | absent
    statut: Mapped[str] = mapped_column(String(10))
    # qr | manuel | auto
    via: Mapped[str] = mapped_column(String(10), default="qr")
    heure: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    justificatif: Mapped[str | None] = mapped_column(Text)
    # aucun | en_attente | accepte | refuse
    justif_statut: Mapped[str] = mapped_column(String(12), default="aucun")

    seance: Mapped[Seance] = relationship(back_populates="presences")
    etudiant: Mapped[User] = relationship()


class CahierEntry(Base):
    __tablename__ = "cahier"

    id: Mapped[int] = mapped_column(primary_key=True)
    seance_id: Mapped[int] = mapped_column(ForeignKey("seances.id"), unique=True)
    titre: Mapped[str] = mapped_column(String(200))
    contenu: Mapped[str] = mapped_column(Text, default="")
    devoir: Mapped[str] = mapped_column(Text, default="")
    auteur_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    valide_par_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    seance: Mapped[Seance] = relationship(back_populates="cahier")
    auteur: Mapped[User] = relationship(foreign_keys=[auteur_id])
    valide_par: Mapped[User | None] = relationship(foreign_keys=[valide_par_id])


class Support(Base):
    __tablename__ = "supports"

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id"), index=True)
    titre: Mapped[str] = mapped_column(String(200))
    fichier_nom: Mapped[str] = mapped_column(String(200))
    fichier_chemin: Mapped[str] = mapped_column(String(300))
    taille: Mapped[int] = mapped_column(Integer, default=0)
    auteur_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    telechargements: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    module: Mapped[Module] = relationship()
    auteur: Mapped[User] = relationship()

    @property
    def extension(self) -> str:
        return self.fichier_nom.rsplit(".", 1)[-1].upper() if "." in self.fichier_nom else "FICHIER"

    @property
    def taille_lisible(self) -> str:
        if self.taille >= 1024 * 1024:
            return f"{self.taille / 1024 / 1024:.1f} Mo".replace(".", ",")
        return f"{max(1, self.taille // 1024)} Ko"


class Fichier(Base):
    """Contenu d'un support de cours rangé dans la base (hébergeurs sans disque permanent)."""

    __tablename__ = "fichiers"

    nom: Mapped[str] = mapped_column(String(80), primary_key=True)
    type_mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    contenu: Mapped[bytes] = mapped_column(LargeBinary)


class Journal(Base):
    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User | None] = relationship()


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    essais: Mapped[int] = mapped_column(Integer, default=0)
    utilise: Mapped[bool] = mapped_column(Boolean, default=False)
