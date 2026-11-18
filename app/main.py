"""Éphéméride 3.0.0 (3.1.0 : variables communes ; 3.2.0 : réglages dans l'appli, sauvegarde et restauration) :
API FastAPI et interface Svelte compilée (format commun du socle).

Routes publiques : /health, les icônes, /api/session, /api/connexion. Tout le reste de /api demande la session
(cookie de l'appli, ou cookie commun « maison_session » posé par l'Anneau). /externe/sauvegarde ne s'ouvre qu'au
jeton commun de la maison (MAISON_JETON), pour la collecte nocturne de l'Anneau. Les fonctions qui lisent et
écrivent les fichiers sont celles de la 2.2.0 (modules exif, analyse, actions) : seule la porte a changé.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import urllib.error
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from . import __version__
from . import reglages as R
from . import etat as E
from . import immich as I
from . import actions as C
from . import maison
from .auth import COMMUN, DUREE, Sessions, en_https

APPLI = "verif_dates"          # cookie « verif_dates_session »
PUBLIQUES = ("/api/connexion", "/api/session")
ICI = Path(__file__).parent
STATIQUE = ICI / "static"
ICONES = {"icone.svg": "image/svg+xml", "icone-180.png": "image/png", "icone-512.png": "image/png",
          "manifest.webmanifest": "application/manifest+json"}
SANS_MOT_DE_PASSE = R.SANS_MOT_DE_PASSE
# « attente » : les tests coupent l'attente après un mauvais mot de passe ;
# « lire_sauvegarde » : les tests y mettent un faux Anneau (argument lire= de maison.lire_sauvegarde).
ETAT: dict = {"attente": True, "lire_sauvegarde": None}


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
    noter_reprises()
    if not R.SCAN_DIR.is_dir():
        print(f"ATTENTION : dossier introuvable {R.SCAN_DIR}")
    yield


app = FastAPI(title="Éphéméride", version=__version__, lifespan=cycle, docs_url=None, redoc_url=None, openapi_url=None)


def noter_reprises() -> None:
    """3.2.0 : une ligne au journal (corrections.csv) pour chaque réglage repris d'une ancienne variable de la stack."""
    for k in R.USAGE.repris:
        texte = f"réglage {k} repris de la variable {R.ANCIENNES_VARIABLES[k]} : {R.USAGE.get(k)}"
        print(texte)
        E.noter_journal([dt.datetime.now().isoformat(timespec="seconds"), "", "", "réglages", "", "réglage", texte])
    R.USAGE.repris.clear()


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
    if chemin.startswith(("/api/", "/externe/")):
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
async def connexion(request: Request, corps: dict = Body(...)):
    s = sessions()
    if ETAT["attente"]:
        await asyncio.sleep(s.attente())
    if not s.verifier_mot_de_passe(str(corps.get("mot_de_passe", ""))):
        raise HTTPException(401, "Mot de passe incorrect.")
    r = JSONResponse({"ok": True})
    r.set_cookie(s.cookie, s.creer(), max_age=DUREE, httponly=True, samesite="strict", secure=en_https(request))
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


# ================================================================ réglages d'usage (3.2.0)

def _vue_reglages() -> dict:
    """Ce que montre l'écran Réglages : les réglages d'usage (modifiables) et les renseignements fixés par la stack."""
    return {"reglages": R.USAGE.tout(),
            "fixes": {"lecture_seule": R.LECTURE_SEULE, "lecture_seule_origine": R.ORIGINE_LECTURE_SEULE,
                      "originaux_gardes": bool(R.ORIGINALS_DIR), "dossier_present": R.SCAN_DIR.is_dir()},
            "tolerance_max": R.TOLERANCE_MAX}


def _sans_tache() -> None:
    """Changer de dossier ou de tolérance pendant une analyse ou une correction mélangerait deux réglages."""
    with E.LOCK:
        if E.STATE.get("running"):
            raise HTTPException(409, "Une tâche est en cours : changer les réglages à la fin.")


def _journal_reglages(origine: str, avant: dict, apres: dict) -> None:
    changes = [f"{k} : {avant.get(k)!r} → {apres.get(k)!r}" for k in apres if avant.get(k) != apres.get(k)]
    if changes:
        E.noter_journal([dt.datetime.now().isoformat(timespec="seconds"), "", "", "réglages", "", "réglage",
                         f"{origine} : " + ", ".join(changes)])


@app.get("/api/reglages")
def lire_reglages():
    return _vue_reglages()


@app.put("/api/reglages")
def changer_reglages(corps: dict = Body(...)):
    """Change le sous-dossier analysé et la tolérance ; effet immédiat. Permis en lecture seule (rien n'est écrit
    dans la Pellicule : seul le fichier reglages.json de l'outil change)."""
    _sans_tache()
    avant = R.USAGE.tout()
    try:
        apres = R.changer(corps)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    _journal_reglages("Réglages", avant, apres)
    return _vue_reglages()


# ================================================================ sauvegarde et restauration (contrat de sauvegarde 1)

@app.get("/externe/sauvegarde")
def externe_sauvegarde(request: Request):
    """Collecte nocturne de l'Anneau : jeton commun seulement ; jeton absent ou faux, ou MAISON_JETON vide : 404."""
    if not maison.jeton_valide(maison.jeton_propose(request.headers), maison.jetons()):
        raise HTTPException(404, "Not Found")
    return maison.sauvegarde(R.APPLI_ID, __version__, R.USAGE.tout(), exclus=R.EXCLUS_SAUVEGARDE)


def _lire_anneau(le: str = "") -> dict:
    """Demande à l'Anneau ; message clair si l'Anneau ne répond pas ou n'a rien."""
    try:
        reponse = maison.lire_sauvegarde(R.APPLI_ID, le, lire=ETAT.get("lire_sauvegarde"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(404, "L'Anneau n'a aucune sauvegarde de ces réglages" + (f" le {le}." if le else ".")) from None
        raise HTTPException(503, f"L'Anneau a refusé la demande ({exc.code}) : réessayer plus tard.") from None
    except Exception as exc:                                   # noqa: BLE001 — Anneau injoignable, réponse illisible
        raise HTTPException(503, f"L'Anneau est injoignable ({exc.__class__.__name__}) : réessayer plus tard.") from None
    if not isinstance(reponse, dict):
        raise HTTPException(503, "Réponse de l'Anneau illisible.")
    return reponse


def _date(le: str) -> str:
    le = (le or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", le):
        raise HTTPException(400, "Date attendue sous la forme AAAA-MM-JJ.")
    return le


def _sauvegarde_du(le: str) -> tuple[dict, list[dict]]:
    sauve = _lire_anneau(le)
    reglages = sauve.get("reglages")
    if not isinstance(reglages, dict):
        raise HTTPException(404, f"L'Anneau n'a aucune sauvegarde de ces réglages le {le}.")
    return sauve, maison.differences(R.USAGE.tout(), reglages, R.EXCLUS_SAUVEGARDE)


@app.get("/api/restaurer")
def restaurer_voir(le: str = ""):
    """Sans « le » : les dates des sauvegardes ; avec « le » : ce qui changerait."""
    if not le:
        dates = [d for d in (_lire_anneau().get("dates") or []) if isinstance(d, str)]
        if not dates:
            raise HTTPException(404, "L'Anneau n'a encore aucune sauvegarde de ces réglages.")
        return {"dates": dates}
    le = _date(le)
    sauve, diffs = _sauvegarde_du(le)
    return {"le": sauve.get("le") or le, "version": sauve.get("version") or "", "differences": diffs}


@app.post("/api/restaurer")
def restaurer(corps: dict = Body(...)):
    """Applique les réglages d'une sauvegarde qui diffèrent de l'actuel. Ne change que reglages.json :
    permis même en lecture seule."""
    le = _date(str(corps.get("le", "")))
    _sans_tache()
    _, diffs = _sauvegarde_du(le)
    avant = R.USAGE.tout()
    if diffs:
        try:
            apres = R.changer({d["cle"]: d["sauve"] for d in diffs})
        except ValueError as exc:
            raise HTTPException(400, f"Sauvegarde du {le} inutilisable : {exc}") from None
        _journal_reglages(f"Restauré depuis la sauvegarde du {le}", avant, apres)
    return _vue_reglages() | {"restaures": len(diffs)}


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
