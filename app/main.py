"""Éphéméride 3.0.0 (3.1.0 : variables communes, voir reglages) : API FastAPI et interface Svelte compilée (format commun du socle).

Routes publiques : /health, les icônes, /api/session, /api/connexion. Tout le reste demande la session
(cookie de l'appli, ou cookie commun « maison_session » posé par l'Anneau). Les fonctions qui lisent et
écrivent les fichiers sont celles de la 2.2.0 (modules exif, analyse, actions) : seule la porte a changé.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__
from . import reglages as R
from . import etat as E
from . import immich as I
from . import actions as C
from .auth import COMMUN, DUREE, Sessions

APPLI = "verif_dates"          # cookie « verif_dates_session »
PUBLIQUES = ("/api/connexion", "/api/session")
ICI = Path(__file__).parent
STATIQUE = ICI / "static"
ICONES = {"icone.svg": "image/svg+xml", "icone-180.png": "image/png", "icone-512.png": "image/png",
          "manifest.webmanifest": "application/manifest+json"}
SANS_MOT_DE_PASSE = "Variable MAISON_PASSWORD (ou VERIF_PASSWORD) absente : à définir dans la stack."
ETAT: dict = {"attente": True}   # les tests coupent l'attente après un mauvais mot de passe


def _fichier_generation() -> Path:
    return R.DATA_DIR / "session_generation"


def _lire_generation() -> int:
    try:
        return int(_fichier_generation().read_text().strip())
    except (OSError, ValueError):
        return 0


def sessions() -> Sessions:
    """Refaites si le mot de passe change (les tests le changent)."""
    s = ETAT.get("sessions")
    if s is None or ETAT.get("mdp") != R.PASSWORD:
        s = Sessions(APPLI, R.PASSWORD, _lire_generation())
        ETAT.update(sessions=s, mdp=R.PASSWORD)
    return s


@asynccontextmanager
async def cycle(app: FastAPI):
    E.load_results()
    E.load_corrected()
    E.load_validated()
    if not R.PASSWORD:
        print("ATTENTION : " + SANS_MOT_DE_PASSE)
    if not R.SCAN_DIR.is_dir():
        print(f"ATTENTION : dossier introuvable {R.SCAN_DIR}")
    yield


app = FastAPI(title="Éphéméride", version=__version__, lifespan=cycle, docs_url=None, redoc_url=None, openapi_url=None)


# ================================================================ sécurité

@app.middleware("http")
async def garde(request: Request, appel):
    chemin = request.url.path
    if chemin.startswith("/api/"):
        if not R.PASSWORD:
            return JSONResponse({"detail": SANS_MOT_DE_PASSE}, status_code=503)
        if chemin not in PUBLIQUES and not sessions().connecte(request.cookies):
            return JSONResponse({"detail": "Connexion nécessaire."}, status_code=401)
    reponse = await appel(request)
    if chemin.startswith("/api/"):
        reponse.headers["Cache-Control"] = "no-store"
    return reponse


@app.exception_handler(E.LectureSeule)
async def _lecture_seule(_: Request, exc: E.LectureSeule):
    return JSONResponse({"detail": str(exc) or "Mode lecture seule : rien n'est écrit."}, status_code=409)


@app.exception_handler(C.Refus)
async def _refus(_: Request, exc: C.Refus):
    return JSONResponse({"detail": str(exc)}, status_code=exc.statut)


# ================================================================ public

@app.get("/health")
def sante():
    """Pour la tuile de l'Anneau : des nombres, aucun nom de fichier ni de dossier."""
    with E.LOCK:
        a_traiter = sum(1 for it in E.STATE["items"] if not (it.get("result") or {}).get("ok"))
        en_cours = bool(E.STATE.get("running"))
    return {"ok": True, "version": __version__, "a_traiter": a_traiter, "en_cours": en_cours}


@app.get("/api/session")
def session(request: Request):
    return {"connecte": sessions().connecte(request.cookies), "version": __version__,
            "lecture_seule": R.LECTURE_SEULE, "lecture_seule_origine": R.ORIGINE_LECTURE_SEULE}


@app.post("/api/connexion")
async def connexion(corps: dict = Body(...)):
    s = sessions()
    if ETAT["attente"]:
        await asyncio.sleep(s.attente())
    if not s.verifier_mot_de_passe(str(corps.get("mot_de_passe", ""))):
        raise HTTPException(401, "Mot de passe incorrect.")
    r = JSONResponse({"ok": True})
    r.set_cookie(s.cookie, s.creer(), max_age=DUREE, httponly=True, samesite="strict")
    return r


@app.post("/api/deconnexion")
def deconnexion():
    s = sessions()
    s.generation += 1
    try:
        _fichier_generation().write_text(str(s.generation))
    except OSError:
        pass
    r = JSONResponse({"ok": True})
    r.delete_cookie(s.cookie)
    r.delete_cookie(COMMUN)      # « Se déconnecter » ferme aussi la connexion commune
    return r


# ================================================================ travail

@app.get("/api/etat")
def etat():
    return C.etat_complet() | {"version": __version__}


@app.get("/api/progression")
def progression():
    return C.progression()


@app.post("/api/analyse")
def analyse(corps: dict = Body(default={})):
    return {"ok": True, "mode": C.lancer_analyse(corps.get("mode", "tout"))}


@app.post("/api/corriger")
def corriger(corps: dict = Body(...)):
    """corps : {"photos": [{"id": …, "date": "AAAA-MM-JJTHH:MM:SS"}, …]}"""
    choix = [(str(p.get("id", "")), str(p.get("date", ""))) for p in corps.get("photos") or [] if isinstance(p, dict)]
    return {"ok": True, "n": C.lancer_corrections(choix)}


@app.post("/api/realigner")
def realigner():
    return {"ok": True, "n": C.lancer_realignement()}


@app.post("/api/valider")
def valider(corps: dict = Body(...)):
    return {"ok": True, "n": C.valider_dossier(str(corps.get("folder", "")))}


@app.post("/api/renommer")
def renommer(corps: dict = Body(...)):
    C.renommer_dossier(str(corps.get("folder", "")), str(corps.get("nouveau") or ""))
    return {"ok": True}


@app.get("/api/miniature/{item_id}")
def miniature(item_id: str):
    item = E.ITEMS.get(item_id) or next((r for r in list(E.CORRECTED.values()) if r["id"] == item_id), None)
    if item is None:
        raise HTTPException(404, "photo inconnue")
    cache = R.THUMBS / f"{item_id}.jpg"
    if not cache.exists():
        I.make_thumb(item, cache)
    if cache.exists() and cache.stat().st_size > 0:
        return FileResponse(cache, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    return Response(I.PLACEHOLDER, media_type="image/svg+xml")


@app.get("/api/journal.csv")
def journal():
    if not R.LOG_FILE.exists():
        raise HTTPException(404, "aucune correction écrite pour l'instant")
    return FileResponse(R.LOG_FILE, media_type="text/csv", filename="corrections.csv")


# ================================================================ interface

@app.get("/{chemin:path}")
def interface(chemin: str):
    if chemin in ICONES and (ICI / chemin).is_file():
        return FileResponse(ICI / chemin, media_type=ICONES[chemin], headers={"Cache-Control": "public, max-age=86400"})
    fichier = (STATIQUE / chemin).resolve() if chemin else None
    if fichier and fichier.is_file() and STATIQUE.resolve() in fichier.parents:
        return FileResponse(fichier)
    index = STATIQUE / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "Interface non compilée."}, status_code=404)
