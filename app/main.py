from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import init_db
from .services import creer_premier_admin
from .routes import admin, auth, commun, enseignant, etudiant, suivi
from .web import Redirection, rediriger, rendre


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if config.STOCKAGE == "local":
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    creer_premier_admin()
    yield


app = FastAPI(title="TDSI · Suivi de cours", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    # Firebase Hosting ne transmet à Cloud Run que le cookie nommé « __session ».
    session_cookie="__session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=config.COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")

for module in (auth, etudiant, enseignant, commun, suivi, admin):
    app.include_router(module.router)


@app.middleware("http")
async def _en_tetes_securite(request: Request, call_next):
    reponse = await call_next(request)
    if not request.url.path.startswith("/static/"):
        # Pages personnelles : jamais mises en cache par le CDN de Firebase ni par le navigateur.
        reponse.headers.setdefault("Cache-Control", "private, no-store")
    reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
    reponse.headers.setdefault("X-Frame-Options", "DENY")
    reponse.headers.setdefault("Referrer-Policy", "same-origin")
    return reponse


@app.exception_handler(Redirection)
async def _redirection(request: Request, exc: Redirection):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "message": "Session expirée, reconnectez-vous."}, status_code=401)
    return rediriger(exc.url)


@app.exception_handler(HTTPException)
async def _erreur_http(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "message": exc.detail}, status_code=exc.status_code)
    titres = {403: "Accès refusé", 404: "Page introuvable"}
    reponse = rendre(request, "erreur.html", titre=titres.get(exc.status_code, "Erreur"), detail=exc.detail)
    reponse.status_code = exc.status_code
    return reponse
