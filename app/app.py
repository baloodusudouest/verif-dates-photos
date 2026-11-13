#!/usr/bin/env python3
"""
Vérification des dates de la Pellicule avant migration vers le stockage Immich.

- Analyse les fichiers de <LOCAL_ROOT>/<SCAN_SUBDIR> avec exiftool
- Compare la date de prise de vue à la date du dossier (AAAA-MM-JJ, sinon AAAA-MM, sinon AAAA)
- Lit aussi la date contenue dans le nom du fichier (IMG_20190714_153000, 2008-08-10-014136052…) :
  elle devient la date proposée si elle concorde avec le dossier, sinon la photo est « à trancher »
- Deux modes : « Scanner les nouveaux » (fichiers nouveaux ou modifiés depuis la dernière analyse,
  les autres sont repris du cache) et « Tout rescanner »
- Cohérence avec les autres photos du dossier (« voisines ») : jour dominant proposé quand le nom
  du fichier ne donne pas de date, et alerte si la date choisie sort de la période des voisines
- Vidéos AVI, MTS/M2TS et MKV (date non inscriptible par exiftool) : converties en MP4 (flux copiés
  sans perte, réencodage seulement si nécessaire), date écrite et vérifiée, puis original supprimé
  (ou déplacé dans ORIGINALS_DIR si défini)
- Photos corrigées retirées de la liste « À traiter » et rangées dans « Déjà corrigées »
- Fichiers dont le contenu ne correspond pas à l'extension (PNG nommé .jpg…) : renommés avec la bonne
  extension avant correction ; JPEG précédés d'octets parasites : nettoyés sans perte (original gardé
  dans DATA_DIR/originaux-nettoyes) ; fichiers illisibles : signalés « abîmé », jamais modifiés
- Actions par dossier : appliquer d'un coup la date du nom, des voisines, du dossier, la date proposée
  ou un même jour à toutes les photos du dossier, puis « Corriger ce dossier »
- Dossier mal daté : « Les dates des photos sont bonnes » (validation sans modification, plus signalées
  tant que le fichier ne change pas) ou renommage du dossier proposé d'après les dates des photos
- Vidéos : dates lues sans le mode rapide d'exiftool (qui ignorait l'atome moov placé après les
  données et faisait passer des vidéos datées pour « sans date ») ; CreationDate toujours écrite ;
  « Réaligner les vidéos » remet les dates QuickTime d'après CreationDate sur les vidéos touchées à tort
- Estimation du temps pour chaque étape (index Immich, recensement, analyse) et au total, à partir
  du nombre de photos dans Immich et des durées mesurées lors de l'analyse précédente
- Barre de progression en direct (index Immich, recensement, analyse fichier par fichier, corrections)
- Affiche une page de vérification avec miniatures (Immich si l'asset est connu, sinon locales)
- Ne corrige QUE les fichiers cochés, puis relit le fichier pour vérifier l'écriture
- Journalise chaque correction dans <DATA_DIR>/corrections.csv
- Demande à Immich de relire les métadonnées des assets corrigés (si l'API est configurée)
"""
__version__ = "2.1.1"

import calendar
import shutil
from collections import Counter
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import threading
from time import sleep as time_sleep
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import hmac
import secrets
from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, session, url_for
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass

# --------------------------------------------------------------------------- configuration
LOCAL_ROOT = Path(os.environ.get("LOCAL_ROOT", "/photos"))
SCAN_DIR = LOCAL_ROOT / os.environ.get("SCAN_SUBDIR", "Pellicule")
IMMICH_URL = os.environ.get("IMMICH_URL", "").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ROOT = os.environ.get("IMMICH_ROOT", "").rstrip("/")  # dossier Photos tel que vu par Immich
TOLERANCE_DAYS = int(os.environ.get("TOLERANCE_DAYS", "1"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Paris"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PORT = int(os.environ.get("PORT", "8099"))
ORIGINALS_DIR = os.environ.get("ORIGINALS_DIR", "").strip()   # vide : originaux convertis supprimés
# 1.11.0 : mot de passe commun aux applis (obligatoire) et mode lecture seule
PASSWORD = os.environ.get("VERIF_PASSWORD", "")
LECTURE_SEULE = os.environ.get("VERIF_LECTURE_SEULE", "").strip().lower() in ("oui", "1", "true", "yes")

THUMBS = DATA_DIR / "thumbs"
RESULTS_FILE = DATA_DIR / "results.json"
LOG_FILE = DATA_DIR / "corrections.csv"
CORRECTED_FILE = DATA_DIR / "corrigees.json"   # photos corrigées (section « Déjà corrigées »)
VOISINES_MIN = 2         # nombre minimal d'autres photos datées pour juger la cohérence
VOISINES_PART = 0.5      # part minimale du jour dominant pour le proposer
CACHE_FILE = DATA_DIR / "cache.json"
META_FILE = DATA_DIR / "mesures.json"   # durées et volumes de la dernière analyse, pour les estimations   # résultat par fichier (taille + date de modification) pour le mode incrémental
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBS.mkdir(parents=True, exist_ok=True)

PHOTO_EXT = {"jpg", "jpeg", "heic", "heif", "png", "webp", "gif", "tif", "tiff"}
VIDEO_EXT = {"mov", "mp4", "m4v", "3gp", "avi", "mkv", "mts", "m2ts"}
RAW_EXT = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "pef", "srw"}
ALL_EXT = PHOTO_EXT | VIDEO_EXT | RAW_EXT
NEEDS_MP4 = {"avi", "mts", "m2ts", "mkv"}
QUICKTIME_EXT = {"mov", "mp4", "m4v", "3gp"}   # exiftool ne sait pas y écrire de date

READ_TAGS = ["-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-FileModifyDate",
             "-FileTypeExtension", "-Error", "-Warning"]
# extensions équivalentes : pas d'alerte si le contenu est de l'une et le nom de l'autre
EXT_EQUIV = [{"jpg", "jpeg"}, {"tif", "tiff"}, {"heic", "heif"}, {"mts", "m2ts"},
             {"mov", "mp4", "m4v", "3gp", "qt"}]
CLEANED_DIR = DATA_DIR / "originaux-nettoyes"
DOUBLONS_DIR = DATA_DIR / "doublons"   # fichiers mal nommés identiques à un fichier déjà présent   # JPEG d'origine avant retrait des octets parasites
DATE_PRIORITY = ["DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate"]
FMT = "%Y-%m-%d %H:%M:%S"

app = Flask(__name__)
LOCK = threading.Lock()


# --------------------------------------------------------------------------- accès (1.11.0)
class LectureSeule(RuntimeError):
    """Écriture refusée : l'outil tourne en lecture seule (VERIF_LECTURE_SEULE)."""


def exiger_ecriture():
    """À appeler en tête de tout ce qui modifie, déplace ou supprime un fichier de la Pellicule."""
    if LECTURE_SEULE:
        raise LectureSeule("Mode lecture seule : aucun fichier n'est modifié.")


def _cle_session() -> bytes:
    """Clé qui signe le cookie de session, gardée dans DATA_DIR pour survivre aux redémarrages."""
    f = DATA_DIR / "cle-session"
    try:
        if f.exists() and f.stat().st_size >= 32:
            return f.read_bytes()
        cle = secrets.token_bytes(48)
        f.write_bytes(cle)
        f.chmod(0o600)
        return cle
    except OSError:
        return secrets.token_bytes(48)


app.secret_key = _cle_session()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_NAME="verif_dates",
                  PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30))
ECHECS = {"n": 0}     # mots de passe faux à la suite : chaque essai attend un peu plus

PAGE_CONNEXION = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Éphéméride</title>
<link rel="icon" href="/icone.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/icone-180.png"><link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Éphéméride">
<style>
:root{color-scheme:dark;--bg:#151311;--card:#1f1c19;--ink:#f2ece2;--mute:#a89e91;--line:#312c27;--bad:#ef7b72;--acc:#7f9cc4}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px;width:min(22rem,calc(100vw - 2rem));display:grid;gap:10px}
h1{font-size:18px;margin:0}p{margin:0;color:var(--mute);font-size:13px}.ko{color:var(--bad);font-weight:600}
input{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font-size:16px}
button{border:0;border-radius:8px;padding:10px 14px;background:var(--acc);color:#fff;font-weight:600;cursor:pointer;font-size:15px}
</style></head><body>
<form method="post" action="{{ url_for('connexion') }}">
  <h1>Éphéméride</h1>
  <p>Cet outil modifie les fichiers de la Pellicule.</p>
  <label for="mdp">Mot de passe (celui de carnet)</label>
  <input id="mdp" name="mot_de_passe" type="password" autocomplete="current-password" autofocus required>
  {% if erreur %}<p class="ko" role="alert">{{ erreur }}</p>{% endif %}
  <button>Se connecter</button>
</form></body></html>"""


COMMUN = "maison_session"   # cookie commun aux applis de la maison, posé par l'Anneau


def connexion_commune() -> bool:
    """Le cookie posé par l'Anneau, signé avec le mot de passe commun : s'il est bon, pas besoin de se reconnecter.
    Il vaut trente jours, ou jusqu'à ce que le mot de passe change."""
    expire, _, signature = (request.cookies.get(COMMUN) or "").partition(".")
    if not (PASSWORD and expire.isdigit() and int(expire) > dt.datetime.now().timestamp()):
        return False
    cle = hashlib.sha256(b"maison-session|" + PASSWORD.encode()).digest()
    return hmac.compare_digest(signature, hmac.new(cle, expire.encode(), hashlib.sha256).hexdigest())


@app.before_request
def garde():
    """Rien ne répond sans session, sauf la page du mot de passe et le contrôle de santé."""
    if request.endpoint in ("connexion", "sante", "icone"):
        return None
    if not PASSWORD:
        return "Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).", 503
    if session.get("ok") or connexion_commune():
        return None
    if request.method == "GET" and request.endpoint == "index":
        return redirect(url_for("connexion"))
    return {"ok": False, "msg": "Connexion nécessaire."}, 401


ICONES = {"icone.svg": "image/svg+xml", "icone-180.png": "image/png", "icone-512.png": "image/png",
          "manifest.webmanifest": "application/manifest+json"}


@app.get("/<nom>")
def icone(nom):
    """Icône de l'appli et manifeste (2.0.2) : quatre fichiers fixes, servis sans session."""
    if nom not in ICONES:
        abort(404)
    return send_file(Path(__file__).parent / nom, mimetype=ICONES[nom], max_age=86400)


@app.get("/health")
def sante():
    return {"ok": True, "version": __version__}


@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if not PASSWORD:
        return "Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).", 503
    if request.method == "GET":
        return redirect(url_for("index")) if session.get("ok") or connexion_commune() else render_template_string(PAGE_CONNEXION, erreur="")
    time_sleep(min(5.0, 0.5 * ECHECS["n"]))
    if hmac.compare_digest(request.form.get("mot_de_passe", "").encode(), PASSWORD.encode()):
        ECHECS["n"] = 0
        session.clear()
        session["ok"] = True
        session.permanent = True
        return redirect(url_for("index"))
    ECHECS["n"] += 1
    return render_template_string(PAGE_CONNEXION, erreur="Mot de passe incorrect."), 401


@app.post("/deconnexion")
def deconnexion():
    session.clear()
    reponse = redirect(url_for("connexion"))
    reponse.delete_cookie(COMMUN)      # « Se déconnecter » ferme aussi la connexion commune
    return reponse
STATE = {
    "running": False,
    "progress": "",
    "done": 0,
    "total": 0,
    "items": [],
    "stats": {},
    "error": None,
    "immich": "",
    "scanned_at": None,
    "phase": "",        # étape en cours (affichée au-dessus de la barre)
    "kind": "",         # "analyse" ou "correction"
    "started": None,    # horodatage de début, pour le temps restant
    "messages": [],     # messages à afficher à la fin d'une correction
    "steps": [],        # étapes de l'analyse : libellé, statut, durée, estimation
}
ITEMS = {}
CORRECTED = {}   # chemin → enregistrement de la correction
VALIDATED = {}   # chemin → signature (taille, date de modification) au moment de la validation
VALIDATED_FILE = DATA_DIR / "validees.json"
STATUS_KEY = {"écart": "ecart", "sans date": "sans_date", "à trancher": "a_trancher", "abîmé": "abime"}
RE_DATE_PREFIX = re.compile(r"^\d{4}(?:[-_.]\d{2}){0,2}\s*(?:[-–_]\s*)?")


# --------------------------------------------------------------------------- utilitaires
def iid(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in VIDEO_EXT:
        return "vidéo"
    if ext in RAW_EXT:
        return "raw"
    return "photo"


def tz_offset(d: dt.datetime) -> str:
    off = TZ.utcoffset(d.replace(tzinfo=None)) or dt.timedelta(0)
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    try:
        d = dt.datetime.strptime(value[:19], FMT)
    except ValueError:
        return None
    if d.year < 1900:
        return None
    return d


def immich_headers():
    return {"x-api-key": IMMICH_API_KEY, "Accept": "application/json"}


def immich_enabled() -> bool:
    return bool(IMMICH_URL and IMMICH_API_KEY and IMMICH_ROOT)


def to_immich_path(local_path: Path) -> str:
    return f"{IMMICH_ROOT}/{local_path.relative_to(LOCAL_ROOT).as_posix()}"


# --------------------------------------------------------------------------- référence de date du dossier
RE_DAY = re.compile(r"^(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)")
RE_MONTH = re.compile(r"^(\d{4})[-_.](\d{2})(?!\d)")
RE_YEAR = re.compile(r"^(\d{4})(?!\d)")
RANK = {"an": 1, "mois": 2, "jour": 3}


def valid_ref(ref) -> bool:
    p, y, m, d = ref
    if not 1900 <= y <= 2100:
        return False
    if p in ("mois", "jour") and not 1 <= m <= 12:
        return False
    if p == "jour":
        try:
            dt.date(y, m, d)
        except ValueError:
            return False
    return True


def folder_ref(parts):
    """Retourne la référence la plus précise trouvée dans les noms de dossiers."""
    ref = None
    for part in parts:
        cand = None
        m = RE_DAY.match(part)
        if m:
            cand = ("jour", int(m[1]), int(m[2]), int(m[3]))
        else:
            m = RE_MONTH.match(part)
            if m:
                cand = ("mois", int(m[1]), int(m[2]), None)
            else:
                m = RE_YEAR.match(part)
                if m:
                    cand = ("an", int(m[1]), None, None)
        if cand and valid_ref(cand) and (ref is None or RANK[cand[0]] >= RANK[ref[0]]):
            ref = cand
    return ref


def ref_label(ref) -> str:
    p, y, m, d = ref
    if p == "jour":
        return f"{d:02d}/{m:02d}/{y}"
    if p == "mois":
        return f"{m:02d}/{y}"
    return str(y)


def ref_bounds(ref):
    p, y, m, d = ref
    if p == "jour":
        s = e = dt.date(y, m, d)
    elif p == "mois":
        s, e = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
    else:
        s, e = dt.date(y, 1, 1), dt.date(y, 12, 31)
    return s, e


def gap_days(ref, d: dt.datetime) -> int:
    s, e = ref_bounds(ref)
    x = d.date()
    if x < s:
        return (s - x).days
    if x > e:
        return (x - e).days
    return 0


def propose(ref, d):
    """Date du dossier + heure d'origine (12:00 si aucune date)."""
    p, y, m, day = ref
    t = d.time().replace(microsecond=0) if d else dt.time(12, 0, 0)
    if p == "jour":
        nd = dt.date(y, m, day)
    elif p == "mois":
        last = calendar.monthrange(y, m)[1]
        nd = dt.date(y, m, min(d.day, last) if d else 1)
    else:
        if d:
            try:
                nd = dt.date(y, d.month, d.day)
            except ValueError:
                nd = dt.date(y, d.month, 28)
        else:
            nd = dt.date(y, 1, 1)
    return dt.datetime.combine(nd, t)


# --------------------------------------------------------------------------- date du nom de fichier
RE_NAME_DT = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"[-_ T.]?([01]\d|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)")
RE_NAME_D = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


def name_date(name: str):
    """Date (et heure si présente) contenue dans le nom du fichier, sinon (None, False)."""
    stem = Path(name).stem
    m = RE_NAME_DT.search(stem)
    if m:
        try:
            return dt.datetime(*map(int, m.groups())), True
        except ValueError:
            pass
    m = RE_NAME_D.search(stem)
    if m:
        try:
            return dt.datetime(*map(int, m.groups()), 12, 0, 0), False
        except ValueError:
            pass
    return None, False


def propose_from_name(nd: dt.datetime, has_time: bool, d):
    """Jour du nom de fichier ; heure du nom si présente, sinon heure EXIF, sinon 12:00."""
    if has_time:
        return nd
    t = d.time().replace(microsecond=0) if d else dt.time(12, 0, 0)
    return dt.datetime.combine(nd.date(), t)


# --------------------------------------------------------------------------- exiftool
def run_exiftool(target: Path, recursive: bool):
    cmd = ["exiftool", "-j", "-q", "-q", "-api", "QuickTimeUTC", "-d", FMT,
           "-i", "@eaDir", "-i", "#recycle", "-i", "#snapshot", *READ_TAGS]
    for e in sorted(ALL_EXT):
        cmd += ["-ext", e]
    if recursive:
        cmd.append("-r")
    cmd.append(str(target))
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot"}
CHUNK = 100   # fichiers par appel exiftool : assez gros pour être rapide, assez petit pour la barre


def list_files(on_progress=None):
    """Recense les fichiers à analyser (ignore les dossiers techniques Synology)."""
    files, last = [], 0
    for root, dirs, names in os.walk(SCAN_DIR):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            if not n.startswith(".") and Path(n).suffix.lstrip(".").lower() in ALL_EXT:
                files.append(Path(root) / n)
        if on_progress and len(files) - last >= 200:
            last = len(files)
            on_progress(len(files), root)
    return files


# --------------------------------------------------------------------------- étapes et estimations
def load_meta():
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_meta(meta):
    try:
        META_FILE.write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        pass


def now_ts():
    return dt.datetime.now().timestamp()


def step_start(key, total=0, total_estime=False):
    """Passe l'étape `key` en cours ; la barre repart de zéro avec son propre chrono."""
    with LOCK:
        for st in STATE["steps"]:
            if st["key"] == key:
                st.update(status="current", started=now_ts())
        STATE.update(phase=next(s["label"] for s in STATE["steps"] if s["key"] == key), done=0,
                     total=total, total_estime=total_estime, started=now_ts(), progress="")


def step_end(key):
    with LOCK:
        for st in STATE["steps"]:
            if st["key"] == key and st["status"] == "current":
                st.update(status="done", duration=now_ts() - st["started"])
                return st["duration"]
    return None


def step_estimate(key, seconds):
    with LOCK:
        for st in STATE["steps"]:
            if st["key"] == key:
                st["estimate"] = None if seconds is None else int(seconds)


def immich_asset_count():
    """Nombre total de photos et vidéos dans Immich (sert à estimer la lecture de l'index)."""
    try:
        r = requests.get(f"{IMMICH_URL}/api/assets/statistics", headers=immich_headers(), timeout=30)
        r.raise_for_status()
        return int(r.json().get("total") or 0) or None
    except Exception:
        return None


def _exiftool_list(paths, rapide):
    argfile = DATA_DIR / "exiftool-args.txt"
    argfile.write_text("\n".join(str(x) for x in paths) + "\n", encoding="utf-8")
    cmd = ["exiftool", "-j", "-q", "-q", *(["-fast2"] if rapide else []), "-api", "QuickTimeUTC", "-d", FMT,
           "-charset", "filename=utf8", *READ_TAGS, "-@", str(argfile)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def run_exiftool_files(paths):
    """Lit les dates d'une liste de fichiers. Le mode rapide (-fast2) n'est utilisé que pour les photos :
    sur une vidéo dont l'atome moov est placé après les données (mdat), il ne voit aucune date."""
    if not paths:
        return []
    photos = [x for x in paths if Path(x).suffix.lstrip(".").lower() not in VIDEO_EXT]
    videos = [x for x in paths if Path(x).suffix.lstrip(".").lower() in VIDEO_EXT]
    return (_exiftool_list(photos, True) if photos else []) + (_exiftool_list(videos, False) if videos else [])


def set_progress(**kw):
    with LOCK:
        STATE.update(**kw)


def same_ext(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a == b or any(a in g and b in g for g in EXT_EQUIV)


def content_ext(path: Path) -> str:
    """Extension correspondant au contenu réel du fichier (selon exiftool), '' si inconnu."""
    p = subprocess.run(["exiftool", "-q", "-q", "-s3", "-FileTypeExtension", str(path)],
                       capture_output=True, text=True)
    return p.stdout.strip().lower()


def same_content(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    ha, hb = hashlib.sha256(), hashlib.sha256()
    with a.open("rb") as fa, b.open("rb") as fb:
        for chunk in iter(lambda: fa.read(1 << 20), b""):
            ha.update(chunk)
        for chunk in iter(lambda: fb.read(1 << 20), b""):
            hb.update(chunk)
    return ha.digest() == hb.digest()


def free_path(p: Path) -> Path:
    n = 2
    cand = p
    while cand.exists():
        cand = p.with_name(f"{p.stem}-{n}{p.suffix}")
        n += 1
    return cand


def move_sidecars(src: Path, dst: Path):
    for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
        if sc.exists():
            target = Path(str(dst) + ".xmp")
            if not target.exists():
                sc.rename(target)


def rename_to_real_ext(item, real: str):
    """Renomme le fichier avec l'extension de son vrai format. Si ce nom est déjà pris :
    - fichier identique octet pour octet → le fichier mal nommé est mis de côté dans DOUBLONS_DIR
      et la date sera écrite sur le fichier déjà bien nommé ;
    - sinon → nom alternatif « nom-ancienneext.vraieext ». Retourne (nouveau chemin, note)."""
    exiger_ecriture()
    src = Path(item["path"])
    dst = src.with_suffix("." + real)
    old_ext = src.suffix.lstrip(".").lower()
    if dst.exists() and same_content(src, dst):
        cote = free_path(DOUBLONS_DIR / src.relative_to(LOCAL_ROOT))
        cote.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(cote))
        for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
            if sc.exists() and sc != dst.with_suffix(".xmp"):
                shutil.move(str(sc), str(free_path(Path(str(cote) + ".xmp"))))
        item.update(path=str(dst), name=dst.name, immich_id=None)
        return dst, f"doublon identique de {dst.name} : mis de côté dans {cote}, date écrite sur {dst.name}"
    if dst.exists():
        dst = free_path(src.with_name(f"{src.stem}-{old_ext}.{real}"))
    src.rename(dst)
    move_sidecars(src, dst)
    item.update(path=str(dst), name=dst.name, immich_id=None)
    return dst, f"renommé en {dst.name}"


def strip_jpeg_header(item):
    """JPEG précédé d'octets parasites : retire ce qui précède le marqueur de début d'image (FF D8 FF).
    Aucun pixel n'est modifié ; l'original est copié dans CLEANED_DIR. Retourne un message ou None."""
    exiger_ecriture()
    src = Path(item["path"])
    data = src.read_bytes()
    pos = data.find(b"\xff\xd8\xff", 0, 65536)
    if pos <= 0:
        return None
    tmp = src.with_name(f".{src.stem}.nettoyage{src.suffix}")
    tmp.write_bytes(data[pos:])
    try:
        with Image.open(tmp) as im:
            im.verify()
    except Exception:
        tmp.unlink(missing_ok=True)
        return None
    backup = CLEANED_DIR / src.relative_to(LOCAL_ROOT)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, backup)
    st = src.stat()
    tmp.replace(src)
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    return f"{pos} octet(s) parasite(s) retiré(s) avant l'image (original gardé dans {backup})"


def write_date_robust(item, nd: dt.datetime):
    """write_date + réparations sûres quand exiftool refuse le fichier :
    1. contenu d'un autre format que l'extension → renommage ; 2. octets parasites → nettoyage ;
    sinon le fichier est déclaré abîmé et n'est pas modifié."""
    exiger_ecriture()
    notes = []
    real = item.get("vrai_ext") or ""
    if real:
        dst, note = rename_to_real_ext(item, real)
        notes.append(note)
    ok, msg = write_date(item, nd)
    if not ok and "Not a valid" in msg:
        real = content_ext(Path(item["path"]))
        cur = Path(item["path"]).suffix.lstrip(".")
        if real and real in ALL_EXT and not same_ext(real, cur):
            dst, note = rename_to_real_ext(item, real)
            notes.append(f"{note} (le contenu est un {real.upper()})")
            ok, msg = write_date(item, nd)
        elif cur.lower() in ("jpg", "jpeg"):
            note = strip_jpeg_header(item)
            if note:
                notes.append(note)
                ok, msg = write_date(item, nd)
    if not ok and ("Not a valid" in msg or "Corrupted" in msg or "format error" in msg.lower()
                   or "Truncated" in msg):
        item.update(status="abîmé", erreur=msg)
        return False, f"fichier abîmé, non modifié : {msg}", notes
    if ok:
        ok, msg = verify(item, nd)
    return ok, msg, notes


def pick_date(rec):
    for tag in DATE_PRIORITY:
        d = parse_date(rec.get(tag))
        if d:
            return d, tag
    return None, None


# --------------------------------------------------------------------------- index Immich
def immich_index(meta=None):
    if not immich_enabled():
        return {}, "API Immich non configurée : miniatures locales, pas de date Immich.", 0
    meta = meta or {}
    total = immich_asset_count()
    estime = total is None and bool(meta.get("index_total"))
    total = total or meta.get("index_total") or 0
    step_start("index", total=total, total_estime=estime)
    idx, page, vus = {}, 1, 0
    try:
        while page:
            r = requests.post(f"{IMMICH_URL}/api/search/metadata", headers=immich_headers(),
                              json={"page": page, "size": 1000}, timeout=120)
            r.raise_for_status()
            assets = r.json().get("assets", {})
            for it in assets.get("items", []):
                vus += 1
                op = it.get("originalPath", "")
                if op.startswith(IMMICH_ROOT + "/"):
                    idx[op] = (it.get("id"), it.get("localDateTime"))
            nxt = assets.get("nextPage")
            page = int(nxt) if nxt else None
            with LOCK:
                if STATE["total"] and vus > STATE["total"]:
                    STATE["total"] = vus
                STATE.update(done=vus, progress=f"{len(idx)} fichiers de la Pellicule reconnus dans Immich")
        return idx, f"Immich : {len(idx)} fichiers de la photothèque indexés.", vus
    except Exception as exc:
        return idx, f"Immich injoignable ou API refusée ({exc}). Analyse faite sur les fichiers seuls.", vus


# --------------------------------------------------------------------------- analyse
def analyse_record(rec, immich_idx, stats):
    path = Path(rec["SourceFile"])
    try:
        rel = path.relative_to(SCAN_DIR)
    except ValueError:
        return None
    stats["fichiers"] += 1
    ref = folder_ref(rel.parts[:-1])
    d, src = pick_date(rec)
    nd, nd_time = name_date(path.name)
    name_ref = ("jour", nd.year, nd.month, nd.day) if nd else None

    erreur = rec.get("Error") or ""
    fte = (rec.get("FileTypeExtension") or "").lower()
    vrai_ext = fte if (fte and fte in ALL_EXT and not same_ext(fte, path.suffix.lstrip("."))) else ""
    if fte and fte not in ALL_EXT and not erreur:
        erreur = f"le contenu n'est pas une photo ni une vidéo ({fte.upper()})"
        fte = ""
    if erreur and not fte:
        # 0. Fichier illisible ou qui n'est pas une photo : signalé, jamais modifié
        status, key, proposed, psrc = "abîmé", "abime", None, ""
    # 1. Le nom du fichier contredit le dossier : toujours à trancher
    elif nd and ref and gap_days(ref, nd) > TOLERANCE_DAYS:
        status, key, proposed = "à trancher", "a_trancher", None
    else:
        # 2. Référence de détection : le dossier, sinon la date du nom du fichier
        det_ref = ref or name_ref
        if det_ref is None:
            stats["sans_ref"] += 1
            if d:
                return None
        if d and det_ref and gap_days(det_ref, d) <= TOLERANCE_DAYS:
            stats["ok"] += 1
            return None
        status, key = ("sans date", "sans_date") if d is None else ("écart", "ecart")
        if nd:
            proposed, psrc = propose_from_name(nd, nd_time, d), "nom du fichier"
        elif ref:
            proposed, psrc = propose(ref, d), "dossier"
        else:
            proposed, psrc = None, ""
    stats[key] += 1
    if status == "à trancher":
        psrc = ""

    immich_id, immich_date = None, None
    if immich_idx:
        hit = immich_idx.get(to_immich_path(path))
        if hit:
            immich_id, immich_date = hit
    gap_ref = ref or name_ref
    folder_choice = propose(ref, d) if ref else None
    name_choice = propose_from_name(nd, nd_time, d) if nd else None
    fmt_in = "%Y-%m-%dT%H:%M:%S"
    return {
        "id": iid(str(path)),
        "path": str(path),
        "name": path.name,
        "folder": str(rel.parent),
        "kind": kind_of(path.suffix.lstrip(".")),
        "status": status,
        "ref": ref_label(ref) if ref else "aucune",
        "date": d.strftime("%d/%m/%Y %H:%M:%S") if d else "",
        "source": src or "",
        "gap": gap_days(gap_ref, d) if (d and gap_ref) else None,
        "name_date": (nd.strftime("%d/%m/%Y %H:%M:%S") if nd_time else nd.strftime("%d/%m/%Y")) if nd else "",
        "choice_folder": folder_choice.strftime(fmt_in) if folder_choice else "",
        "choice_name": name_choice.strftime(fmt_in) if name_choice else "",
        "immich_id": immich_id,
        "immich_date": (immich_date or "")[:16].replace("T", " "),
        "has_creationdate": "CreationDate" in rec,
        "erreur": erreur,
        "vrai_ext": vrai_ext,
        "alerte": rec.get("Warning") or "",
        "proposed_base": proposed.strftime(fmt_in) if proposed else "",
        "proposed_src_base": psrc,
        "proposed": proposed.strftime(fmt_in) if proposed else "",
        "proposed_src": psrc,
        "result": None,
    }


def neighbor_info(rec):
    """(dossier, date EXIF ISO ou None, cohérente avec le dossier) : sert à juger les voisines."""
    path = Path(rec["SourceFile"])
    try:
        rel = path.relative_to(SCAN_DIR)
    except ValueError:
        return None
    ref = folder_ref(rel.parts[:-1])
    d, _ = pick_date(rec)
    coherent = bool(d) and (ref is None or gap_days(ref, d) <= TOLERANCE_DAYS)
    return [str(rel.parent), d.strftime(FMT) if d else None, coherent]


def apply_neighbors(items, voisins):
    """Ajoute à chaque photo signalée la période de ses voisines et, à défaut de date dans le nom,
    propose le jour dominant des voisines (heure d'origine conservée)."""
    fmt_in = "%Y-%m-%dT%H:%M:%S"
    for it in items:
        it["convertir"] = Path(it["path"]).suffix.lstrip(".").lower() in NEEDS_MP4
        it["proposed"], it["proposed_src"] = it.get("proposed_base", it["proposed"]), it.get("proposed_src_base", "")
        it["voisins"], it["choice_voisins"] = None, ""
        dates = sorted(dt.datetime.strptime(d, FMT) for p, d, ok in voisins.get(it["folder"], [])
                       if ok and d and p != it["path"])
        if len(dates) < VOISINES_MIN:
            continue
        jours = Counter(x.date() for x in dates)
        jour, nb = jours.most_common(1)[0]
        vmin, vmax = dates[0].date(), dates[-1].date()
        if vmin == vmax:
            label = f"toutes le {jour:%d/%m/%Y} ({len(dates)} photos)"
        else:
            label = f"{jour:%d/%m/%Y} pour {nb}/{len(dates)} photos · du {vmin:%d/%m/%Y} au {vmax:%d/%m/%Y}"
        it["voisins"] = {"n": len(dates), "min": vmin.isoformat(), "max": vmax.isoformat(), "label": label}
        if nb / len(dates) >= VOISINES_PART:
            try:
                t = dt.datetime.strptime(it["date"], "%d/%m/%Y %H:%M:%S").time() if it["date"] else dt.time(12)
            except ValueError:
                t = dt.time(12)
            choix = dt.datetime.combine(jour, t)
            it["choice_voisins"] = choix.strftime(fmt_in)
            # priorité : nom du fichier > voisines > dossier
            if it["status"] not in ("à trancher", "abîmé") and it["proposed_src"] != "nom du fichier":
                it["proposed"], it["proposed_src"] = it["choice_voisins"], "autres photos du dossier"


def load_validated():
    try:
        VALIDATED.update(json.loads(VALIDATED_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass


def save_validated():
    tmp = VALIDATED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(VALIDATED, ensure_ascii=False), encoding="utf-8")
    tmp.replace(VALIDATED_FILE)


def propose_folder(folder: str, entries):
    """Si la majorité des photos datées contredit la date du dossier, propose un nouveau chemin
    « année/AAAA-MM-JJ évènement » (ou AAAA-MM / AAAA selon la dispersion des dates)."""
    parts = Path(folder).parts
    ref = folder_ref(parts)
    if not parts or ref is None:
        return None
    dates = []
    for p, d, _ok in entries:
        if d:
            dates.append(dt.datetime.strptime(d, FMT))
        else:
            nd, _ = name_date(Path(p).name)
            if nd:
                dates.append(nd)
    if len(dates) < 1:
        return None
    hors = [x for x in dates if gap_days(ref, x) > TOLERANCE_DAYS]
    if len(hors) * 2 < len(dates):
        return None
    n = len(dates)
    jour, nj = Counter(x.date() for x in dates).most_common(1)[0]
    mois, nm = Counter((x.year, x.month) for x in dates).most_common(1)[0]
    an, na = Counter(x.year for x in dates).most_common(1)[0]
    if nj * 2 > n:
        prefixe, label = jour.strftime("%Y-%m-%d"), f"{nj}/{n} photos datées du {jour:%d/%m/%Y}"
    elif nm * 2 > n:
        prefixe, label = f"{mois[0]}-{mois[1]:02d}", f"{nm}/{n} photos datées de {mois[1]:02d}/{mois[0]}"
    elif na * 2 > n:
        prefixe, label = str(an), f"{na}/{n} photos datées de {an}"
    else:
        return None
    evenement = RE_DATE_PREFIX.sub("", parts[-1]).strip()
    nom = f"{prefixe} {evenement}".strip()
    annee = prefixe[:4]
    if len(parts) >= 2 and re.fullmatch(r"\d{4}", parts[-2]):
        nouveau = Path(*parts[:-2], annee, nom)
    else:
        nouveau = Path(*parts[:-1], nom)
    if str(nouveau) == folder:
        return None
    return {"nouveau": str(nouveau).replace("\\", "/"), "label": label}


def load_corrected():
    try:
        CORRECTED.update(json.loads(CORRECTED_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass


def save_corrected():
    tmp = CORRECTED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(CORRECTED, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CORRECTED_FILE)


def cache_params():
    """Réglages qui changent le résultat : s'ils changent, le cache n'est plus valable."""
    return {"version": __version__.rsplit(".", 1)[0], "tol": TOLERANCE_DAYS, "root": str(SCAN_DIR),
            "immich": IMMICH_ROOT}


def load_cache():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("params") == cache_params():
            return data.get("files", {})
    except Exception:
        pass
    return {}


def save_cache(files):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"params": cache_params(), "files": files}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def file_sig(path: Path):
    try:
        st = path.stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def scan_worker(mode="tout"):
    """mode « tout » : tout relire ; mode « nouveaux » : seuls les fichiers nouveaux ou modifiés."""
    try:
        stats = {"fichiers": 0, "ok": 0, "ecart": 0, "sans_date": 0, "a_trancher": 0, "abime": 0, "sans_ref": 0}
        meta = load_meta()
        fichiers_prev = meta.get("files_total")
        estime = lambda n, cle: (n * meta[cle]) if (n and meta.get(cle) is not None) else None
        with LOCK:
            STATE["steps"] = [
                {"key": "index", "label": "1/3 · Lecture de l'index Immich", "status": "todo",
                 "estimate": None if not immich_enabled() else estime(meta.get("index_total"), "index_rate")},
                {"key": "walk", "label": "2/3 · Recensement des fichiers", "status": "todo",
                 "estimate": estime(fichiers_prev, "walk_rate")},
                {"key": "analyse", "label": "3/3 · Analyse des dates", "status": "todo",
                 "estimate": estime(fichiers_prev, "analyse_rate") if mode == "tout" else None},
            ]
            if not immich_enabled():
                STATE["steps"][0].update(status="skipped")

        t0 = now_ts()
        immich_idx, immich_msg, nb_immich = immich_index(meta)
        duree_index = step_end("index")

        step_start("walk", total=fichiers_prev or 0, total_estime=bool(fichiers_prev))
        set_progress(immich=immich_msg)

        def avance(n, dossier):
            with LOCK:
                if STATE["total"] and n > STATE["total"]:
                    STATE["total"] = n
                try:
                    STATE.update(done=n, progress=str(Path(dossier).relative_to(LOCAL_ROOT)))
                except ValueError:
                    STATE.update(done=n)
        files = list_files(avance)
        duree_walk = step_end("walk")
        old_cache = load_cache() if mode == "nouveaux" else {}
        new_cache, items, to_scan, reused = {}, [], [], 0
        voisins = {}   # dossier → [(chemin, date, cohérente)]
        for f in files:
            sig = file_sig(f)
            entry = old_cache.get(str(f))
            if sig and entry and entry.get("sig") == sig:
                # fichier inchangé depuis la dernière analyse : résultat repris tel quel
                reused += 1
                for k, v in entry["stats"].items():
                    stats[k] += v
                item = entry.get("item")
                if item:
                    item = dict(item, result=None)
                    if immich_idx:   # l'index Immich a pu changer : id et date relus
                        hit = immich_idx.get(to_immich_path(f))
                        item["immich_id"], item["immich_date"] = (
                            (hit[0], (hit[1] or "")[:16].replace("T", " ")) if hit else (None, ""))
                    items.append(item)
                if entry.get("v"):
                    voisins.setdefault(entry["v"][0], []).append((str(f), entry["v"][1], entry["v"][2]))
                new_cache[str(f)] = entry
            else:
                to_scan.append(f)

        step_estimate("analyse", estime(len(to_scan), "analyse_rate"))
        if mode == "nouveaux":
            with LOCK:
                STATE["steps"][2]["label"] = (f"3/3 · Analyse des dates — {len(to_scan):,} nouveaux ou modifiés, "
                                              f"{reused:,} repris").replace(",", " ")
        step_start("analyse", total=len(to_scan))
        t_analyse = now_ts()
        for i in range(0, len(to_scan), CHUNK):
            chunk = to_scan[i:i + CHUNK]
            set_progress(progress=str(chunk[0].parent.relative_to(LOCAL_ROOT)))
            for rec in run_exiftool_files(chunk):
                before = dict(stats)
                item = analyse_record(rec, immich_idx, stats)
                if item:
                    items.append(item)
                path = Path(rec["SourceFile"])
                v = neighbor_info(rec)
                if v:
                    voisins.setdefault(v[0], []).append((str(path), v[1], v[2]))
                sig = file_sig(path)
                if sig:
                    delta = {k: stats[k] - before[k] for k in stats if stats[k] != before[k]}
                    new_cache[str(path)] = {"sig": sig, "stats": delta, "item": item, "v": v}
            set_progress(done=i + len(chunk))

        apply_neighbors(items, voisins)
        gardes = []
        for it in items:   # photos validées telles quelles : plus signalées tant que le fichier n'a pas changé
            sig = VALIDATED.get(it["path"])
            if sig and sig == file_sig(Path(it["path"])):
                stats[STATUS_KEY.get(it["status"], "ecart")] -= 1
                stats["valide"] = stats.get("valide", 0) + 1
            else:
                gardes.append(it)
        items = gardes
        dossiers_signales = {it["folder"] for it in items}
        folders = {}
        for dossier in dossiers_signales:
            prop = propose_folder(dossier, voisins.get(dossier, []))
            if prop:
                folders[dossier] = prop
        for it in items:   # photo déjà corrigée mais toujours en écart : signalée à nouveau
            rec = CORRECTED.get(it["path"])
            it["deja"] = rec["quand"] if rec else ""
        items.sort(key=lambda it: (it["folder"], it["name"]))
        with LOCK:
            STATE.update(items=items, stats=stats, folders=folders, scanned_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
                         progress="Terminé", error=None, last_mode=mode,
                         messages=([f"Analyse incrémentale : {len(to_scan)} fichier(s) relu(s), "
                                    f"{reused} repris de l'analyse précédente."] if mode == "nouveaux" else []))
            ITEMS.clear()
            ITEMS.update({it["id"]: it for it in items})
        duree_analyse = now_ts() - t_analyse
        step_end("analyse")
        # mesures pour estimer la prochaine analyse
        if nb_immich and duree_index:
            meta.update(index_total=nb_immich, index_rate=duree_index / nb_immich)
        if files and duree_walk is not None:
            meta.update(files_total=len(files), walk_rate=duree_walk / len(files))
        if len(to_scan) >= 50:
            meta.update(analyse_rate=duree_analyse / len(to_scan))
        meta["last_total_s"] = round(now_ts() - t0)
        save_meta(meta)
        save_cache(new_cache)
        save_results()
    except Exception as exc:
        set_progress(error=str(exc))
    finally:
        set_progress(running=False, kind="", phase="")


def save_results():
    with LOCK:
        data = {k: STATE.get(k) for k in ("items", "stats", "immich", "scanned_at", "folders")}
    RESULTS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_results():
    if RESULTS_FILE.exists():
        try:
            data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
            STATE.update(data)
            ITEMS.update({i["id"]: i for i in STATE["items"]})
        except Exception:
            pass


# --------------------------------------------------------------------------- correction
def probe_duration(path: Path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def remux_mp4(src: Path, dst: Path):
    """Copie sans perte vers MP4 ; à défaut réencode l'audio (AAC), puis tout (H.264 + AAC)."""
    base = ["ffmpeg", "-y", "-loglevel", "error", "-fflags", "+genpts", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "-1", "-movflags", "+faststart"]
    essais = [
        ("copie sans perte", ["-c", "copy"]),
        ("vidéo copiée, son réencodé", ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]),
        ("réencodée", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k"]),
    ]
    derniere = ""
    for libelle, opts in essais:
        dst.unlink(missing_ok=True)
        p = subprocess.run(base + opts + [str(dst)], capture_output=True, text=True)
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return libelle, ""
        derniere = (p.stderr or "").strip().splitlines()[-1:] or ["échec ffmpeg"]
        derniere = derniere[0][:200]
    dst.unlink(missing_ok=True)
    return None, derniere


def convert_and_date(item, nd: dt.datetime):
    """AVI/MTS/MKV → MP4 daté. L'original n'est supprimé qu'après vérification de la copie et de sa date."""
    exiger_ecriture()
    src = Path(item["path"])
    final = src.with_suffix(".mp4")
    if final.exists():
        final = src.with_name(f"{src.stem}-{src.suffix.lstrip('.').lower()}.mp4")
    if final.exists():
        return False, f"{final.name} existe déjà : conversion annulée", None
    tmp = src.with_name(f".{src.stem}.conversion.mp4")
    mode, err = remux_mp4(src, tmp)
    if not mode:
        return False, f"conversion MP4 impossible : {err}", None
    d_src, d_tmp = probe_duration(src), probe_duration(tmp)
    if d_src and (not d_tmp or abs(d_src - d_tmp) > max(1.0, d_src * 0.01)):
        tmp.unlink(missing_ok=True)
        return False, f"durée différente après conversion ({d_src:.1f} s → {d_tmp or 0:.1f} s) : original conservé", None
    tmp_item = dict(item, path=str(tmp), has_creationdate=False)
    ok, msg = write_date(tmp_item, nd)
    if ok:
        ok, msg = verify(tmp_item, nd)
    if not ok:
        tmp.unlink(missing_ok=True)
        return False, f"date non écrite dans le MP4 : {msg}", None
    tmp.rename(final)
    os.utime(final, (nd.timestamp(), nd.timestamp()))
    # sidecar éventuel de l'original : suit la vidéo
    for sc in (Path(str(src) + ".xmp"), src.with_suffix(".xmp")):
        if sc.exists():
            sc.rename(Path(str(final) + ".xmp"))
    if ORIGINALS_DIR:
        dest = Path(ORIGINALS_DIR) / src.relative_to(LOCAL_ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        sort = f"original déplacé dans {dest}"
    else:
        src.unlink()
        sort = "original supprimé"
    return True, f"converti en {final.name} ({mode}), {msg}, {sort}", final


def write_date(item, nd: dt.datetime):
    exiger_ecriture()
    path = Path(item["path"])
    v = nd.strftime("%Y:%m:%d %H:%M:%S")
    off = tz_offset(nd)
    ext = path.suffix.lstrip(".").lower()
    args = ["exiftool", "-overwrite_original", "-q", "-m"]
    if ext in VIDEO_EXT:
        args += ["-api", "QuickTimeUTC"]
        for tag in ("CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate",
                    "MediaCreateDate", "MediaModifyDate"):
            args.append(f"-QuickTime:{tag}={v}")
        if item.get("has_creationdate") or ext in QUICKTIME_EXT:
            # CreationDate (Keys) passe avant CreateDate chez Immich : toujours l'écrire
            args.append(f"-Keys:CreationDate={v}{off}")
    else:
        args += [f"-AllDates={v}", f"-OffsetTimeOriginal={off}", f"-OffsetTimeDigitized={off}",
                 f"-OffsetTime={off}"]
    args += [f"-FileModifyDate={v}{off}", str(path)]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()[:300]

    # sidecars XMP : Immich les lit en priorité
    for sc in (Path(str(path) + ".xmp"), path.with_suffix(".xmp")):
        if sc.exists():
            subprocess.run(["exiftool", "-overwrite_original", "-q", "-m",
                            f"-XMP-exif:DateTimeOriginal={v}{off}", f"-XMP-xmp:CreateDate={v}{off}",
                            str(sc)], capture_output=True, text=True)
    return True, ""


def verify(item, nd: dt.datetime):
    """Relit le fichier après écriture et compare à la date demandée."""
    recs = run_exiftool(Path(item["path"]), False)
    if not recs:
        return False, "relecture impossible"
    d, src = pick_date(recs[0])
    if d is None:
        return False, "aucune date relue"
    if abs((d - nd).total_seconds()) > 60:
        return False, f"relu {d.strftime('%d/%m/%Y %H:%M:%S')} ({src})"
    return True, f"relu {d.strftime('%d/%m/%Y %H:%M:%S')} ({src})"


def log_correction(item, nd, ok, msg):
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["horodatage", "fichier", "ancienne_date", "source", "nouvelle_date", "statut", "detail"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), item["path"], item["date"],
                    item["source"], nd.strftime("%d/%m/%Y %H:%M:%S"), "ok" if ok else "échec", msg])


def immich_refresh(asset_ids):
    if not (immich_enabled() and asset_ids):
        return ""
    try:
        r = requests.post(f"{IMMICH_URL}/api/assets/jobs", headers=immich_headers(),
                          json={"assetIds": asset_ids, "name": "refresh-metadata"}, timeout=60)
        r.raise_for_status()
        return f"Immich : relecture des métadonnées demandée pour {len(asset_ids)} fichier(s)."
    except Exception as exc:
        return f"Immich : relecture non demandée ({exc}). Lance un scan de la bibliothèque externe."


# --------------------------------------------------------------------------- miniatures
PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220">'
               '<rect width="100%" height="100%" fill="#ddd"/><text x="50%" y="50%" fill="#777" '
               'font-family="sans-serif" font-size="16" text-anchor="middle">aperçu indisponible</text></svg>')


def save_thumb(img: Image.Image, cache: Path):
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((320, 320))
    img.save(cache, "JPEG", quality=80)


def make_thumb(item, cache: Path):
    path = Path(item["path"])
    ext = path.suffix.lstrip(".").lower()
    if item.get("immich_id") and immich_enabled():
        try:
            r = requests.get(f"{IMMICH_URL}/api/assets/{item['immich_id']}/thumbnail",
                             headers=immich_headers(), params={"size": "thumbnail"}, timeout=30)
            r.raise_for_status()
            save_thumb(Image.open(io.BytesIO(r.content)), cache)
            return
        except Exception:
            pass
    try:
        if ext in VIDEO_EXT:
            for ss in ("1", "0"):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", ss, "-i", str(path),
                                "-frames:v", "1", "-vf", "scale=320:-2", str(cache)],
                               capture_output=True, timeout=60)
                if cache.exists() and cache.stat().st_size > 0:
                    return
        elif ext in RAW_EXT:
            for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
                p = subprocess.run(["exiftool", "-b", tag, str(path)], capture_output=True, timeout=60)
                if p.stdout:
                    save_thumb(Image.open(io.BytesIO(p.stdout)), cache)
                    return
        else:
            save_thumb(Image.open(path), cache)
    except Exception:
        pass


# --------------------------------------------------------------------------- pages
PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Éphéméride</title>
<link rel="icon" href="/icone.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/icone-180.png"><link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Éphéméride">
<style>
/* 2.0.0 : habillage commun aux applis (celui de carnet) : fonds neutres chauds, sombre par défaut,
   clair quand l'appareil le demande. Couleur de l'outil : bleu ardoise. Les noms des variables et
   des classes d'avant sont gardés : le script de la page et les gestes n'ont pas changé. */
:root{color-scheme:dark;--bg:#151311;--card:#1f1c19;--ink:#f2ece2;--mute:#a89e91;--line:#312c27;--bord:#4b443c;
  --warn:#e39a4b;--bad:#ef7b72;--ok:#7fc493;--acc:#7f9cc4;--sur-acc:#10161f;--ombre:rgb(0 0 0 / .5)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,'Segoe UI',sans-serif;-webkit-font-smoothing:antialiased}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:1.9rem;font-weight:300;letter-spacing:-.03em;line-height:1.15;margin:0 12px 0 0;flex-basis:100%}main{padding:16px 20px;max-width:1400px}
button,.btn{border:1px solid var(--acc);border-radius:8px;padding:8px 14px;background:var(--acc);color:var(--sur-acc);font-weight:600;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}button:disabled{opacity:.45;cursor:not-allowed}
button.sec,.btn.sec{background:transparent;color:var(--ink);border:1px solid var(--bord);font-weight:500}button.sec:hover,.btn.sec:hover{background:var(--card)}
.stats{color:var(--mute);font-size:13px}.msg{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:14px}
.folder{margin:22px 0 8px;display:flex;flex-direction:column;gap:8px}.folder h2{font-size:15px;margin:0}
.folder-titre{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.folder{scroll-margin-top:150px}.plier{padding:2px 9px;font-size:13px}.grid.replie{display:none!important}
.folder.replie .folder-actions{display:none}.resume{font-size:13px;font-weight:600;color:var(--ok)}
.folder-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.folder-actions button{padding:5px 10px;font-size:13px}.folder-actions .jour{display:inline-flex;gap:4px}
.folder-actions input[type=date]{padding:4px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.dossier-info{font-size:12px}.folder-actions.renommage input[type=text]{flex:1;min-width:200px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:170px;object-fit:cover;background:var(--line)}.body{padding:10px;font-size:13px;display:flex;flex-direction:column;gap:4px}
.name{font-weight:600;word-break:break-all}.tag{display:inline-block;border-radius:5px;padding:1px 6px;font-size:12px;font-weight:600;color:#fff}
.tag{color:var(--bg)}.t-ecart{background:var(--warn)}.t-sans{background:var(--bad)}.t-abime{background:var(--mute)}.t-trancher{background:var(--acc)}.mute{color:var(--mute)}
.t-ok{background:var(--ok)}.card.done{opacity:.9}.actions{display:flex;gap:8px;flex-wrap:wrap}
.coh{font-size:12px;font-weight:600}.coh-ok{color:var(--ok)}.coh-ko{color:var(--warn)}
.choix{display:flex;gap:6px;flex-wrap:wrap}.choix button{padding:4px 8px;font-size:12px}
input[type=datetime-local]{width:100%;padding:5px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
label.sel{display:flex;gap:6px;align-items:center;font-weight:600;margin-top:4px}
.res-ok{color:var(--ok);font-weight:600}.res-ko{color:var(--bad);font-weight:600}
.prog{display:flex;flex-direction:column;gap:8px}.prog[hidden]{display:none}.prog-head,.prog-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.prog-foot span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}
.bar{height:12px;border-radius:6px;background:var(--line);overflow:hidden;position:relative}
.fill{height:100%;width:0;background:var(--acc);border-radius:6px;transition:width .4s ease}
.bar.indet .fill{width:30%;position:absolute;animation:indet 1.2s ease-in-out infinite}
@keyframes indet{0%{left:-30%}100%{left:100%}}
.scanbtns{display:flex;gap:8px}.realign{border-color:var(--warn)}.realign button{margin-left:8px}
.steps{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:4px;font-size:13px}
.steps:empty{display:none}.steps li{display:flex;justify-content:space-between;gap:10px;padding:4px 8px;border-radius:6px}
.steps li.current{background:color-mix(in srgb,var(--acc) 12%,transparent);font-weight:600}
.steps li.done{color:var(--ok)}.steps li.todo,.steps li.skipped{color:var(--mute)}
.prog-total{font-weight:700;text-align:right}.prog-total:empty{display:none}
select{padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
/* navigation commune : rail à gauche sur PC, pilule d'icônes en bas sur téléphone */
.rail{position:fixed;z-index:20;left:50%;transform:translateX(-50%);bottom:calc(.6rem + env(safe-area-inset-bottom));display:flex;gap:.2rem;padding:.35rem;
  border-radius:9999px;background:color-mix(in srgb,var(--bg) 82%,transparent);border:1px solid var(--line);box-shadow:0 10px 30px var(--ombre);backdrop-filter:blur(14px)}
.rail .marque{display:none}.rail a{display:flex;align-items:center;gap:.7rem;height:3rem;padding:0 1rem;border-radius:9999px;color:var(--mute);font-weight:600;text-decoration:none;white-space:nowrap}
.rail a svg{width:1.45rem;height:1.45rem;flex:none}.rail a span.nom{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.rail a[aria-current=page]{background:var(--ink);color:var(--bg)}.rail .pastille{min-width:1.3rem;height:1.2rem;padding:0 .35rem;border-radius:9999px;background:var(--acc);color:var(--sur-acc);font-size:.7rem;font-weight:700;line-height:1.2rem;text-align:center}
.contenu{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
@media (min-width:900px){.rail{top:0;bottom:0;left:0;transform:none;width:13rem;flex-direction:column;gap:.25rem;padding:1.25rem .75rem;border-radius:0;border:0;border-right:1px solid var(--line);background:var(--bg);box-shadow:none;backdrop-filter:none}
  .rail .marque{display:block;font-size:1.25rem;font-weight:300;letter-spacing:-.02em;padding:0 .85rem 1.1rem;color:var(--ink)}
  .rail a{height:2.75rem;padding:0 .85rem}.rail a span.nom{position:static;width:auto;height:auto;clip:auto;flex:1}
  .rail a[aria-current=page]{background:var(--card);color:var(--ink)}.contenu{margin-left:13rem;padding-bottom:2rem}}
/* tuiles : une par motif, à la place du filtre déroulant */
.tuiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem;flex-basis:100%}
@media (min-width:700px){.tuiles{grid-template-columns:repeat(4,minmax(0,1fr))}}@media (min-width:1200px){.tuiles{grid-template-columns:repeat(7,minmax(0,1fr))}}
.tuile{display:grid;gap:.1rem;text-align:left;padding:.7rem .85rem;border-radius:12px;background:var(--card);border:1px solid var(--line);color:var(--ink);font-weight:500}
.tuile:hover,.tuile[aria-pressed=true]{border-color:var(--acc)}.tuile[aria-pressed=true]{background:color-mix(in srgb,var(--acc) 14%,var(--card))}
.tuile .chiffre{font-size:1.6rem;font-weight:300;letter-spacing:-.03em;line-height:1.1}.tuile .nom-tuile{font-size:.85rem;color:var(--mute)}.tuile.vide{background:transparent}.tuile.vide .chiffre{color:var(--mute)}
#filtre{display:none}.vue{padding:16px 20px;max-width:60rem}.vue h1{flex-basis:auto;margin-bottom:.8rem}
.bloc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}.bloc h2{font-size:1.05rem;margin:0 0 .4rem}
.bloc dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1rem;margin:0}.bloc dt{color:var(--mute)}.bloc dd{margin:0}
.voyant{display:inline-block;width:.6rem;height:.6rem;border-radius:9999px;background:var(--bord);margin-right:.4rem}.voyant.allume{background:var(--ok)}
[hidden]{display:none!important}
/* l'en-tête (outils et tuiles) ne reste collé en haut que sur grand écran : sur téléphone il masquerait les photos */
.folder{scroll-margin-top:270px}
@media (max-width:899px){header{position:static}.folder{scroll-margin-top:12px}}
</style></head><body>
<nav class="rail" aria-label="Sections">
  <span class="marque">Éphéméride</span>
  <a href="#a-traiter" data-vue="a-traiter" aria-label="À traiter"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 13.5 6.2 5.6A1.6 1.6 0 0 1 7.8 4.5h8.4a1.6 1.6 0 0 1 1.6 1.1L20 13.5"/><path d="M4 13.5V18a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 20 18v-4.5h-4.6l-1.2 2.2h-4.4l-1.2-2.2z"/></svg><span class="nom">À traiter</span>{% if pending %}<span class="pastille">{{ pending|length if pending|length < 1000 else '999+' }}</span>{% endif %}</a>
  <a href="#corrigees" data-vue="corrigees" aria-label="Corrigées"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.3 2.6 2.6 5-5.4"/></svg><span class="nom">Corrigées</span></a>
  <a href="#connexion" data-vue="connexion" aria-label="Connexion"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="2.6"/><circle cx="5" cy="6" r="1.8"/><circle cx="19" cy="6" r="1.8"/><circle cx="5" cy="18" r="1.8"/><circle cx="19" cy="18" r="1.8"/><path d="m6.4 7.2 3.6 3M17.6 7.2l-3.6 3M6.4 16.8l3.6-3M17.6 16.8l-3.6-3"/></svg><span class="nom">Connexion</span></a>
  <a href="#reglages" data-vue="reglages" aria-label="Réglages"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 7.5h6M15 7.5h4M5 16.5h3M12 16.5h7"/><circle cx="13" cy="7.5" r="2"/><circle cx="10" cy="16.5" r="2"/></svg><span class="nom">Réglages</span></a>
</nav>
<div class="contenu">
<div id="vue-travail">
<header>
  <h1 id="titre-vue">À traiter</h1>
  <form id="scanform" method="post" action="{{ url_for('scan') }}" class="scanbtns">
  {% if state.running %}
    <button disabled>{{ 'Correction en cours…' if state.kind == 'correction' else 'Analyse en cours…' }}</button>
  {% elif has_cache %}
    <button name="mode" value="nouveaux" title="Analyse seulement les fichiers ajoutés ou modifiés depuis la dernière analyse">Scanner les nouveaux</button>
    <button class="sec" name="mode" value="tout" onclick="return confirm('Relire tous les fichiers de la Pellicule ? Cela peut prendre du temps.')" title="Relit tous les fichiers">Tout rescanner</button>
  {% else %}
    <button name="mode" value="tout">Lancer l'analyse</button>
  {% endif %}
  </form>
  {% if pending or corrected %}
  <select id="filtre" onchange="filtrer()">
    <option value="tous">À traiter — tout ({{ pending|length }})</option>
    <option value="écart">Écarts de date ({{ counts['écart'] }})</option>
    <option value="sans date">Sans date EXIF ({{ counts['sans date'] }})</option>
    <option value="à trancher">À trancher, nom ≠ dossier ({{ counts['à trancher'] }})</option>
    <option value="incoherent">Date proposée incohérente avec les voisines</option>
    <option value="abîmé">Fichiers abîmés ({{ counts['abîmé'] }})</option>
    <option value="extension">Extension incorrecte</option>
    <option value="corrigees">Déjà corrigées ({{ corrected|length }})</option>
  </select>
  <div class="tuiles" id="tuiles" role="group" aria-label="Motifs à traiter">
    <button type="button" class="tuile" data-filtre="tous" aria-pressed="true"><span class="chiffre">{{ pending|length }}</span><span class="nom-tuile">Tout</span></button>
    <button type="button" class="tuile{{ '' if counts['écart'] else ' vide' }}" data-filtre="écart" aria-pressed="false"><span class="chiffre">{{ counts['écart'] }}</span><span class="nom-tuile">Écarts de date</span></button>
    <button type="button" class="tuile{{ '' if counts['sans date'] else ' vide' }}" data-filtre="sans date" aria-pressed="false"><span class="chiffre">{{ counts['sans date'] }}</span><span class="nom-tuile">Sans date</span></button>
    <button type="button" class="tuile{{ '' if counts['à trancher'] else ' vide' }}" data-filtre="à trancher" aria-pressed="false"><span class="chiffre">{{ counts['à trancher'] }}</span><span class="nom-tuile">À trancher (nom ≠ dossier)</span></button>
    <button type="button" class="tuile vide" data-filtre="incoherent" aria-pressed="false"><span class="chiffre" id="n-incoherent">0</span><span class="nom-tuile">Hors période des voisines</span></button>
    <button type="button" class="tuile vide" data-filtre="extension" aria-pressed="false"><span class="chiffre" id="n-extension">0</span><span class="nom-tuile">Extension incorrecte</span></button>
    <button type="button" class="tuile{{ '' if counts['abîmé'] else ' vide' }}" data-filtre="abîmé" aria-pressed="false"><span class="chiffre">{{ counts['abîmé'] }}</span><span class="nom-tuile">Fichiers abîmés</span></button>
  </div>
  {% endif %}
  {% if pending %}
  <span class="actions">
  <button class="sec" type="button" onclick="cocher(true)">Tout cocher (visible)</button>
  <button class="sec" type="button" onclick="cocher(false)">Tout décocher</button>
  <button form="f" onclick="return confirmer()" {% if state.running %}disabled{% endif %}>Corriger la sélection</button>
  </span>
  {% endif %}
  <span class="stats">
  {% if state.scanned_at %}Analyse du {{ state.scanned_at }} · {{ state.stats.fichiers }} fichiers ·
  {{ state.stats.ok }} OK · {{ state.stats.ecart }} écarts · {{ state.stats.sans_date }} sans date · {{ state.stats.a_trancher or 0 }} à trancher ·{% if state.stats.abime %} {{ state.stats.abime }} abîmé(s) ·{% endif %}
  {{ state.stats.sans_ref }} hors dossier daté · tolérance {{ tol }} j{% endif %}{% if corrected %} · {{ corrected|length }} corrigée(s){% endif %}{% if state.stats and state.stats.valide %} · {{ state.stats.valide }} validée(s) telles quelles{% endif %}</span>
</header>
<main>
<div id="prog" class="msg prog" {% if not state.running %}hidden{% endif %}>
  <div class="prog-head"><strong id="prog-phase">{{ state.phase }}</strong><span id="prog-count" class="mute"></span></div>
  <div class="bar"><div id="prog-fill" class="fill"></div></div>
  <div class="prog-foot"><span id="prog-detail" class="mute"></span><span id="prog-eta" class="mute"></span></div>
  <ol id="prog-steps" class="steps"></ol>
  <div id="prog-total" class="prog-total"></div>
</div>
{% if lecture_seule %}<div class="msg realign" role="status"><strong>Mode lecture seule.</strong> L'outil analyse et propose des dates, mais ne modifie, ne déplace et ne supprime aucun fichier (VERIF_LECTURE_SEULE).</div>{% endif %}
{% if state.error %}<div class="msg res-ko">Erreur : {{ state.error }}</div>{% endif %}
{% if state.immich %}<div class="msg mute">{{ state.immich }}</div>{% endif %}
{% for m in messages %}<div class="msg">{{ m }}</div>{% endfor %}
{% if a_realigner %}<div class="msg realign"><strong>{{ a_realigner }} vidéo(s) modifiée(s) à tort par une version précédente.</strong>
  Elles avaient déjà une date d'origine (CreationDate) que l'outil ne voyait pas : seuls les champs QuickTime secondaires
  et la date du fichier ont été changés (Immich affiche toujours la date d'origine).
  <form method="post" action="{{ url_for('realigner') }}" style="display:inline"><button {% if state.running %}disabled{% endif %}
  onclick="return confirm('Remettre les dates QuickTime et la date du fichier de ces vidéos sur leur date d\u2019origine (CreationDate) ?')">Réaligner ces vidéos</button></form></div>{% endif %}
{% if not pending and state.scanned_at and not state.running %}<div class="msg res-ok" id="rien">Aucune photo à traiter.</div>{% endif %}
<form id="f" method="post" action="{{ url_for('apply') }}">
{% for folder, group in groups %}
  {% set modifiables = group|rejectattr('status', 'equalto', 'abîmé')|list %}
  <div class="folder" data-folder data-nom="{{ folder }}">
    <div class="folder-titre"><button class="sec plier" type="button" onclick="plier(this)" aria-expanded="true" title="Replier ou déplier ce dossier">▾</button><h2>{{ folder }}</h2><span class="mute">dossier : {{ group[0].ref }} · {{ group|length }} photo(s)</span><span class="resume"></span></div>
    {% if modifiables %}
    <div class="folder-actions">
      <span class="mute">Tout le dossier :</span>
      {% if modifiables|selectattr('choice_name')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'name')">Date du nom ({{ modifiables|selectattr('choice_name')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('choice_voisins')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'voisins')">Voisines ({{ modifiables|selectattr('choice_voisins')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('choice_folder')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'folder')">Date du dossier ({{ modifiables|selectattr('choice_folder')|list|length }})</button>{% endif %}
      {% if modifiables|selectattr('proposed')|list %}<button class="sec" type="button" onclick="appliquerDossier(this,'proposed')">Dates proposées ({{ modifiables|selectattr('proposed')|list|length }})</button>{% endif %}
      <span class="jour"><input type="date" class="dossier-jour" aria-label="Même jour pour tout le dossier"><button class="sec" type="button" onclick="appliquerDossier(this,'jour')">Même jour pour toutes</button></span>
      <button class="sec" type="button" onclick="cocherDossier(this)">Cocher le dossier</button>
      <button type="button" onclick="corrigerDossier(this)">Corriger ce dossier</button>
      <button class="sec" type="button" onclick="validerDossier(this)" title="Le dossier est mal daté mais les photos ont la bonne date : rien n'est modifié">Les dates des photos sont bonnes</button>
      <span class="dossier-info mute"></span>
    </div>
    <div class="folder-actions renommage">
      <span class="mute">{% if state.folders and state.folders.get(folder) %}Dossier mal daté ? {{ state.folders[folder].label }} — renommer en :{% else %}Renommer le dossier :{% endif %}</span>
      <input type="text" class="dossier-nom" value="{{ state.folders[folder].nouveau if state.folders and state.folders.get(folder) else folder }}" size="40" aria-label="Nouveau nom du dossier">
      <button class="sec" type="button" onclick="renommerDossier(this)">Renommer le dossier</button>
    </div>
    {% endif %}
  </div>
  <div class="grid">
  {% for it in group %}
    <div class="card" data-status="{{ it.status }}" data-c-name="{{ it.choice_name }}" data-c-voisins="{{ it.choice_voisins }}"
         data-c-folder="{{ it.choice_folder }}" data-c-proposed="{{ it.proposed }}" data-heure="{{ it.date[11:19] if it.date else '12:00:00' }}" data-ext="{{ 1 if it.vrai_ext else 0 }}" data-convertir="{{ 1 if it.convertir else 0 }}"{% if it.voisins %} data-vmin="{{ it.voisins.min }}" data-vmax="{{ it.voisins.max }}"{% endif %}>
      <img loading="lazy" src="{{ url_for('thumb', item_id=it.id) }}" alt="">
      <div class="body">
        <div class="name">{{ it.name }}</div>
        <div><span class="tag {{ 't-sans' if it.status == 'sans date' else ('t-trancher' if it.status == 'à trancher' else ('t-abime' if it.status == 'abîmé' else 't-ecart')) }}">{{ it.status }}</span>
          <span class="mute">{{ it.kind }}{% if it.gap %} · {{ it.gap }} j d'écart{% endif %}</span></div>
        <div>Fichier : {{ it.date or '—' }} {% if it.source %}<span class="mute">({{ it.source }})</span>{% endif %}</div>
        {% if it.name_date %}<div>Nom du fichier : {{ it.name_date }}</div>{% endif %}
        {% if it.immich_date %}<div class="mute">Immich : {{ it.immich_date }}</div>{% endif %}
        <div class="mute">Voisines : {{ it.voisins.label if it.voisins else "pas assez d'autres photos datées dans le dossier" }}</div>
        {% if it.status == 'abîmé' %}<div class="res-ko">Fichier illisible ({{ it.erreur }}) : rien ne sera modifié. À remplacer depuis une sauvegarde.</div>{% endif %}
        {% if it.vrai_ext %}<div class="res-ko" style="font-weight:500">⚠ Extension incorrecte : le contenu est un {{ it.vrai_ext|upper }}. Le fichier sera renommé en .{{ it.vrai_ext }} avant d'écrire la date (Immich le verra comme un nouveau fichier). Si ce nom est déjà pris : nom alternatif, ou mise de côté si c'est un doublon identique.</div>{% endif %}
        {% if it.alerte and it.status != 'abîmé' %}<div class="mute">exiftool : {{ it.alerte }}</div>{% endif %}
        {% if it.convertir %}<div class="res-ko" style="font-weight:500">⚠ Format où la date ne peut pas être écrite : la vidéo sera convertie en .mp4 (sans perte si possible), puis l'original {{ 'déplacé' if originals_dir else 'supprimé' }}. Immich la verra comme un nouveau fichier.</div>{% endif %}
        {% if it.deja %}<div class="res-ko">Déjà corrigée le {{ it.deja }}, écart toujours présent</div>{% endif %}
        {% if it.result and not it.result.ok %}<div class="res-ko">✗ échec — {{ it.result.msg }}</div>{% endif %}
        {% if it.status != 'abîmé' %}
        {% if it.status == 'à trancher' or not it.proposed %}
        <div class="mute">Choisis la date à écrire :</div>
        <div class="choix">
          {% if it.choice_folder %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_folder }}')">Dossier ({{ it.ref }})</button>{% endif %}
          {% if it.choice_name %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_name }}')">Nom ({{ it.name_date[:10] }})</button>{% endif %}
          {% if it.choice_voisins %}<button class="sec" type="button" onclick="choisir(this,'{{ it.choice_voisins }}')">Voisines ({{ it.choice_voisins[8:10] }}/{{ it.choice_voisins[5:7] }}/{{ it.choice_voisins[:4] }})</button>{% endif %}
        </div>
        {% else %}
        <div class="mute">Date proposée ({{ it.proposed_src }}) :</div>
        {% endif %}
        <input type="datetime-local" step="1" name="date_{{ it.id }}" value="{{ it.proposed }}" oninput="coherence(this)">
        <div class="coh"></div>
        <label class="sel"><input type="checkbox" name="sel" value="{{ it.id }}"> Corriger</label>
        {% endif %}
      </div>
    </div>
  {% endfor %}
  </div>
{% endfor %}
</form>
<section id="corrigees" hidden>
{% if not corrected %}<div class="msg mute">Aucune photo corrigée pour l'instant.</div>{% endif %}
{% for folder, group in cgroups %}
  <div class="folder"><h2>{{ folder }}</h2><span class="mute">{{ group|length }} corrigée(s)</span></div>
  <div class="grid">
  {% for r in group %}
    <div class="card done">
      <img loading="lazy" src="{{ url_for('thumb', item_id=r.id) }}" alt="">
      <div class="body">
        <div class="name">{{ r.name }}</div>
        <div><span class="tag t-ok">corrigée</span> <span class="mute">{{ r.kind }} · le {{ r.quand }}</span></div>
        <div class="mute">Avant : {{ r.avant }}</div>
        <div>Après : <strong>{{ r.apres }}</strong></div>
        {% if r.note %}<div class="mute">{{ r.note.split(', relu')[0] }}</div>{% endif %}
      </div>
    </div>
  {% endfor %}
  </div>
{% endfor %}
</section>
</main>
</div>
<section class="vue" id="vue-connexion" hidden>
  <h1>Connexion</h1>
  <div class="bloc"><h2><span class="voyant{{ ' allume' if dossier_present else '' }}"></span>La Pellicule</h2>
    <dl><dt>Dossier analysé</dt><dd>{{ dossier }}{{ '' if dossier_present else ' (introuvable)' }}</dd>
      <dt>Dernière analyse</dt><dd>{{ state.scanned_at or 'jamais' }}</dd>
      {% if state.scanned_at %}<dt>Fichiers</dt><dd>{{ state.stats.fichiers }}, dont {{ state.stats.ok }} sans rien à signaler</dd>{% endif %}
      <dt>Écriture</dt><dd>{{ 'lecture seule : aucun fichier n’est modifié' if lecture_seule else 'les dates sont écrites dans les fichiers, sur validation' }}</dd></dl></div>
  <div class="bloc"><h2><span class="voyant{{ ' allume' if immich_configure else '' }}"></span>Immich</h2>
    {% if immich_configure %}<p class="mute">L'outil lit dans Immich la date connue et les miniatures, et lui demande de relire les métadonnées après une correction. Il n'y écrit rien d'autre.</p>
    {% if state.immich %}<p>{{ state.immich }}</p>{% endif %}
    {% else %}<p class="mute">Non configuré (IMMICH_URL, IMMICH_API_KEY) : l'outil fonctionne sans, avec ses propres miniatures.</p>{% endif %}</div>
</section>
<section class="vue" id="vue-reglages" hidden>
  <h1>Réglages</h1>
  <div class="bloc"><h2>Règles</h2>
    <dl><dt>Tolérance</dt><dd>{{ tol }} jour(s) entre la date de la photo et celle du dossier (TOLERANCE_DAYS)</dd>
      <dt>Vidéos converties</dt><dd>{{ 'l’original est gardé à part (ORIGINALS_DIR)' if originals_dir else 'l’original est supprimé après vérification du MP4 (régler ORIGINALS_DIR pour le garder)' }}</dd>
      <dt>Lecture seule</dt><dd>{{ 'oui (VERIF_LECTURE_SEULE)' if lecture_seule else 'non' }}</dd></dl></div>
  <div class="bloc"><h2>Journal</h2>
    <p class="mute">Chaque correction écrite, avec l'ancienne et la nouvelle date.</p>
    <p><a class="btn sec" href="{{ url_for('journal') }}">Télécharger corrections.csv</a></p></div>
  <div class="mute" style="display:flex;justify-content:space-between;align-items:center;gap:1rem"><span>version {{ version }}</span>
    <form method="post" action="{{ url_for('deconnexion') }}"><button class="sec">Se déconnecter</button></form></div>
</section>
</div>
<script>
function visibles(){return [...document.querySelectorAll('.card')].filter(c=>c.style.display!=='none')}
function cocher(v){visibles().forEach(c=>{const b=c.querySelector('input[type=checkbox]');if(b)b.checked=v})}
function grilleDe(btn){return btn.closest('[data-folder]').nextElementSibling}
function cocherDossier(btn){grilleDe(btn).querySelectorAll('.card').forEach(c=>{
  if(c.style.display==='none')return;const b=c.querySelector('input[type=checkbox]');if(b)b.checked=true})}
function appliquerDossier(btn,type){
  const entete=btn.closest('[data-folder]'),info=entete.querySelector('.dossier-info');let jour='';
  if(type==='jour'){jour=entete.querySelector('.dossier-jour').value;if(!jour){alert('Choisis d\u2019abord le jour.');return}}
  let faits=0,sautes=0;
  grilleDe(btn).querySelectorAll('.card').forEach(c=>{
    if(c.style.display==='none')return;
    const inp=c.querySelector('input[type=datetime-local]'),b=c.querySelector('input[name=sel]');if(!inp||!b)return;
    const k={name:'cName',voisins:'cVoisins',folder:'cFolder',proposed:'cProposed'}[type];
    const v=type==='jour'?jour+'T'+(c.dataset.heure||'12:00:00'):c.dataset[k];
    if(!v){sautes++;return}
    inp.value=v;coherence(inp);b.checked=true;faits++});
  info.textContent=faits+' photo(s) réglée(s) et cochée(s)'+(sautes?', '+sautes+' sans date de ce type (inchangées)':'')+' — vérifie puis « Corriger ce dossier ».';
  if(faits){replier(entete,'✓ '+faits+' photo(s) réglée(s) et cochée(s)'+(sautes?' · '+sautes+' inchangée(s)':''));suivant(entete)}}
function replier(entete,texte){entete.classList.add('replie');entete.nextElementSibling.classList.add('replie');
  const b=entete.querySelector('.plier');b.textContent='▸';b.setAttribute('aria-expanded','false');
  entete.querySelector('.resume').textContent=texte||''}
function deplier(entete){entete.classList.remove('replie');entete.nextElementSibling.classList.remove('replie');
  const b=entete.querySelector('.plier');b.textContent='▾';b.setAttribute('aria-expanded','true');entete.querySelector('.resume').textContent=''}
function plier(btn){const e=btn.closest('[data-folder]');e.classList.contains('replie')?deplier(e):replier(e,'replié')}
function suivant(entete){const tous=[...document.querySelectorAll('#f [data-folder]')].filter(h=>h.style.display!=='none');
  const prochain=tous.slice(tous.indexOf(entete)+1).find(h=>!h.classList.contains('replie'));
  (prochain||entete).scrollIntoView({behavior:'smooth',block:'start'})}
async function poster(url,corps){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
  let j={};try{j=await r.json()}catch(e){}return [r.ok,j]}
async function validerDossier(btn){const nom=btn.closest('[data-folder]').dataset.nom;
  if(!confirm('Les photos de « '+nom+' » gardent leur date actuelle et ne seront plus signalées (sauf les photos sans date). Aucun fichier n\u2019est modifié. Continuer ?'))return;
  const [ok,j]=await poster('{{ url_for('valider') }}',{folder:nom});
  if(!ok){alert('Échec : '+(j.msg||'erreur'));return}location.replace('{{ url_for('index') }}')}
async function renommerDossier(btn){const h=btn.closest('[data-folder]'),nom=h.dataset.nom,neuf=h.querySelector('.dossier-nom').value.trim();
  if(!neuf||neuf===nom){alert('Indique le nouveau nom du dossier.');return}
  if(!confirm('Renommer « '+nom+' » en « '+neuf+' » sur le NAS ?\\n\\nImmich verra ces photos comme de nouveaux fichiers. Une analyse des fichiers déplacés est relancée ensuite.'))return;
  const [ok,j]=await poster('{{ url_for('renommer') }}',{folder:nom,nouveau:neuf});
  if(!ok){alert('Échec : '+(j.msg||'erreur'));return}location.replace('{{ url_for('index') }}')}
function corrigerDossier(btn){
  const grille=grilleDe(btn),autres=[...document.querySelectorAll('input[name=sel]')].filter(b=>!grille.contains(b));
  if(!grille.querySelector('input[name=sel]:checked')){alert('Aucune photo cochée dans ce dossier.');return}
  autres.forEach(b=>b.disabled=true);
  if(confirmer())document.getElementById('f').submit();else autres.forEach(b=>b.disabled=false)}
function filtrer(){const f=document.getElementById('filtre').value,corr=f==='corrigees';
  document.getElementById('corrigees').hidden=!corr;document.getElementById('f').hidden=corr;
  const rien=document.getElementById('rien');if(rien)rien.hidden=corr;
  document.querySelectorAll('.actions').forEach(a=>a.style.display=corr?'none':'');
  document.querySelectorAll('#f .card').forEach(c=>{
    let show=f==='tous'||(f==='incoherent'?c.dataset.coh==='0':f==='extension'?c.dataset.ext==='1':c.dataset.status===f);c.style.display=show?'':'none'});
  document.querySelectorAll('#f [data-folder]').forEach(h=>{const g=h.nextElementSibling;
  const any=[...g.querySelectorAll('.card')].some(c=>c.style.display!=='none');h.style.display=g.style.display=any?'':'none'});
  try{localStorage.setItem('verif-filtre',f)}catch(e){}}
const TOL={{ tol }},JOUR=86400000;
function fr(iso){return iso.slice(8,10)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4)}
function coherence(inp){const c=inp.closest('.card'),out=c.querySelector('.coh');if(!out)return;
  if(!c.dataset.vmin||!inp.value){out.textContent='';out.className='coh';delete c.dataset.coh;return}
  const d=Date.parse(inp.value.slice(0,10)+'T00:00:00Z'),a=Date.parse(c.dataset.vmin+'T00:00:00Z'),b=Date.parse(c.dataset.vmax+'T00:00:00Z');
  const ok=d>=a-TOL*JOUR&&d<=b+TOL*JOUR;c.dataset.coh=ok?'1':'0';out.className='coh '+(ok?'coh-ok':'coh-ko');
  out.textContent=ok?'✓ cohérente avec les autres photos du dossier'
    :'⚠ hors de la période des autres photos ('+(c.dataset.vmin===c.dataset.vmax?fr(c.dataset.vmin):fr(c.dataset.vmin)+' – '+fr(c.dataset.vmax))+')'}
document.querySelectorAll('#f input[type=datetime-local]').forEach(coherence);
function choisir(btn,v){const c=btn.closest('.body'),inp=c.querySelector('input[type=datetime-local]');inp.value=v;coherence(inp);
  const b=c.querySelector('input[type=checkbox]');if(b)b.checked=true}
try{const f0=localStorage.getItem('verif-filtre'),sel=document.getElementById('filtre');
  if(f0&&sel&&f0!=='corrigees'&&[...sel.options].some(o=>o.value===f0)){sel.value=f0;filtrer()}}catch(e){}
function fmt(s){s=Math.max(0,Math.round(s));if(s<60)return s+' s';const m=Math.round(s/60);
  return m<60?m+' min':Math.floor(m/60)+' h '+String(m%60).padStart(2,'0')}
function duree(s){return s==null?'':'≈ '+fmt(s)+' restantes'}
function etapes(st){const ol=document.getElementById('prog-steps');ol.innerHTML='';
  (st.steps||[]).forEach(x=>{const li=document.createElement('li');li.className=x.status;
    let droite='';
    if(x.status==='done')droite='✓ terminé en '+fmt(x.duration||0);
    else if(x.status==='current')droite='en cours'+(x.remaining!=null?' · ≈ '+fmt(x.remaining):' · estimation en cours…');
    else if(x.status==='skipped')droite='sans objet';
    else droite=x.estimate!=null?'≈ '+fmt(x.estimate)+' (d’après la dernière analyse)':'durée inconnue (1re analyse)';
    const a=document.createElement('span');a.textContent=(x.status==='done'?'✓ ':x.status==='current'?'▶ ':'○ ')+x.label;
    const b=document.createElement('span');b.textContent=droite;li.append(a,b);ol.append(li)});
  document.getElementById('prog-total').textContent=(st.steps&&st.steps.length)
    ?(st.total_remaining!=null?'Temps restant estimé au total : ≈ '+fmt(st.total_remaining):'Temps total : estimation disponible dès que les étapes en cours auront avancé'):''}
let wasRunning={{ 'true' if state.running else 'false' }};
async function suivre(){
  let st;try{st=await (await fetch('{{ url_for('progress') }}',{cache:'no-store'})).json()}catch(e){return setTimeout(suivre,3000)}
  const box=document.getElementById('prog');
  if(st.running){
    wasRunning=true;box.hidden=false;
    document.getElementById('prog-phase').textContent=st.phase||'…';
    const bar=box.querySelector('.bar'),fill=document.getElementById('prog-fill');
    if(st.total>0){bar.classList.remove('indet');const pc=Math.min(99,Math.floor(st.done*100/st.total));fill.style.width=pc+'%';
      document.getElementById('prog-count').textContent=st.done.toLocaleString('fr-FR')+' / '+(st.total_estime?'≈ ':'')+st.total.toLocaleString('fr-FR')+' · '+pc+' %';}
    else{bar.classList.add('indet');fill.style.width='';
      document.getElementById('prog-count').textContent=st.done?st.done.toLocaleString('fr-FR')+' trouvés':'';}
    etapes(st);
    document.getElementById('prog-detail').textContent=st.progress||'';
    document.getElementById('prog-eta').textContent=duree(st.remaining);
    setTimeout(suivre,1500);
  } else if(wasRunning){location.replace('{{ url_for('index') }}')}
}
if(wasRunning)suivre();
// 2.0.0 : les tuiles pilotent le filtre d'avant ; le rail montre une vue à la fois
function compterTuiles(){const cartes=[...document.querySelectorAll('#f .card')];
  const maj=(id,n)=>{const e=document.getElementById(id);if(!e)return;e.textContent=n;e.closest('.tuile').classList.toggle('vide',!n)};
  maj('n-incoherent',cartes.filter(c=>c.dataset.coh==='0').length);maj('n-extension',cartes.filter(c=>c.dataset.ext==='1').length)}
function tuileActive(f){document.querySelectorAll('#tuiles .tuile').forEach(t=>t.setAttribute('aria-pressed',t.dataset.filtre===f?'true':'false'))}
let dernierFiltre='tous';
document.querySelectorAll('#tuiles .tuile').forEach(t=>t.addEventListener('click',()=>{
  const sel=document.getElementById('filtre');if(!sel)return;dernierFiltre=t.dataset.filtre;sel.value=dernierFiltre;filtrer();tuileActive(dernierFiltre)}));
function vue(){let v=(location.hash||'#a-traiter').slice(1);if(!['a-traiter','corrigees','connexion','reglages'].includes(v))v='a-traiter';
  document.querySelectorAll('.rail a').forEach(a=>{if(a.dataset.vue===v)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')});
  const travail=v==='a-traiter'||v==='corrigees';
  document.getElementById('vue-travail').hidden=!travail;document.getElementById('vue-connexion').hidden=v!=='connexion';document.getElementById('vue-reglages').hidden=v!=='reglages';
  if(!travail)return;
  document.getElementById('titre-vue').textContent=v==='corrigees'?'Corrigées':'À traiter';
  const sel=document.getElementById('filtre'),tuiles=document.getElementById('tuiles'),scan=document.getElementById('scanform');
  if(tuiles)tuiles.hidden=v==='corrigees';if(scan)scan.hidden=v==='corrigees';
  if(sel){sel.value=v==='corrigees'?'corrigees':dernierFiltre;filtrer();if(v!=='corrigees')tuileActive(dernierFiltre)}}
window.addEventListener('hashchange',vue);
try{const f1=document.getElementById('filtre');if(f1&&f1.value!=='corrigees')dernierFiltre=f1.value}catch(e){}
compterTuiles();document.querySelectorAll('#f input[type=datetime-local]').forEach(i=>i.addEventListener('input',compterTuiles));vue();
function confirmer(){
  const vides=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>!b.closest('.body').querySelector('input[type=datetime-local]').value).length;
  if(vides){alert(vides+' photo(s) à trancher cochée(s) sans date choisie.');return false}const n=document.querySelectorAll('input[name=sel]:checked:not(:disabled)').length;
  if(!n){alert('Aucun fichier coché.');return false}
  const ko=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>b.closest('.card').dataset.coh==='0').length;
  if(ko&&!confirm(ko+' photo(s) cochée(s) ont une date hors de la période des autres photos du dossier. Continuer quand même ?'))return false;
  const conv=[...document.querySelectorAll('input[name=sel]:checked:not(:disabled)')].filter(b=>b.closest('.card').dataset.convertir==='1').length;
  return confirm(n+' fichier(s) vont être modifiés directement sur le NAS (sans copie _original).'
    +(conv?'\\n\\n'+conv+' vidéo(s) AVI/MTS/MKV seront converties en MP4 et leur original {{ 'déplacé' if originals_dir else 'SUPPRIMÉ' }}.':'')+'\\n\\nContinuer ?')}
</script>
</body></html>"""


@app.get("/")
def index():
    with LOCK:
        items = list(STATE["items"])
        state = dict(STATE)
        corrected_all = list(CORRECTED.values())
    pending = [it for it in items if not (it.get("result") or {}).get("ok")]
    pending_paths = {it["path"] for it in pending}
    corrected = sorted((r for r in corrected_all if r["path"] not in pending_paths),
                       key=lambda r: (r["folder"], r["name"]))
    groups, cgroups = {}, {}
    for it in pending:
        groups.setdefault(it["folder"], []).append(it)
    for r in corrected:
        cgroups.setdefault(r["folder"], []).append(r)
    counts = {k: sum(1 for it in pending if it["status"] == k) for k in ("écart", "sans date", "à trancher", "abîmé")}
    messages = request.args.getlist("m") + list(state.get("messages") or [])
    return render_template_string(PAGE, state=state, pending=pending, groups=list(groups.items()),
                                  corrected=corrected, cgroups=list(cgroups.items()), counts=counts,
                                  tol=TOLERANCE_DAYS, messages=messages, version=__version__,
                                  has_cache=CACHE_FILE.exists(), originals_dir=bool(ORIGINALS_DIR),
                                  lecture_seule=LECTURE_SEULE, dossier=SCAN_DIR.name, dossier_present=SCAN_DIR.is_dir(),
                                  immich_configure=immich_enabled(),
                                  a_realigner=0 if state["running"] else len(videos_a_realigner()))


@app.post("/scan")
def scan():
    with LOCK:
        if STATE["running"]:
            return redirect(url_for("index"))
        STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Démarrage…",
                     progress="", done=0, total=0, started=None, steps=[], total_estime=False)
    mode = "nouveaux" if request.form.get("mode") == "nouveaux" and CACHE_FILE.exists() else "tout"
    if mode == "tout":
        for f in THUMBS.glob("*.jpg"):
            f.unlink(missing_ok=True)
    threading.Thread(target=scan_worker, args=(mode,), daemon=True).start()
    return redirect(url_for("index"))


def apply_worker(jobs):
    """Corrige les fichiers un par un en mettant la barre de progression à jour."""
    ok_count, ko_count, refresh = 0, 0, []
    try:
        set_progress(phase="Correction des dates", total=len(jobs), done=0,
                     started=dt.datetime.now().timestamp())
        for n, (item, nd) in enumerate(jobs, 1):
            set_progress(progress=item["name"])
            if nd is None:
                item["result"] = {"ok": False, "msg": "date saisie invalide"}
                ko_count += 1
            else:
                if Path(item["path"]).suffix.lstrip(".").lower() in NEEDS_MP4:
                    set_progress(progress=f"Conversion en MP4 : {item['name']}")
                    ok, msg, new_path = convert_and_date(item, nd)
                    log_correction(item, nd, ok, msg)
                    if ok:
                        item.update(path=str(new_path), name=new_path.name, immich_id=None)
                elif item.get("status") == "abîmé":
                    ok, msg = False, "fichier abîmé : non modifié"
                else:
                    try:
                        ok, msg, notes = write_date_robust(item, nd)
                    except OSError as exc:
                        ok, msg, notes = False, f"renommage impossible : {exc}", []
                    if notes:
                        msg = " ; ".join(notes) + (" — " + msg if msg else "")
                    log_correction(item, nd, ok, msg)
                item["result"] = {"ok": ok, "msg": msg}
                THUMBS.joinpath(f"{item['id']}.jpg").unlink(missing_ok=True)
                if ok:
                    with LOCK:
                        CORRECTED[item["path"]] = {
                            "id": item["id"], "path": item["path"], "name": item["name"],
                            "folder": item["folder"], "kind": item["kind"], "immich_id": item.get("immich_id"),
                            "avant": item["date"] or "sans date", "source": item.get("source", ""),
                            "apres": nd.strftime("%d/%m/%Y %H:%M:%S"),
                            "note": msg.split(" — relu")[0] if (msg.startswith("converti") or msg.startswith("renommé")
                                                               or "parasite" in msg or msg.startswith("doublon")) else "",
                            "quand": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
                    save_corrected()
                    ok_count += 1
                    if item.get("immich_id"):
                        refresh.append(item["immich_id"])
                else:
                    ko_count += 1
            set_progress(done=n)
        save_results()
        msgs = [f"{ok_count} fichier(s) corrigé(s) et vérifié(s), {ko_count} échec(s). Journal : {LOG_FILE}"]
        r = immich_refresh(refresh)
        if r:
            msgs.append(r)
        set_progress(messages=msgs)
    except Exception as exc:
        set_progress(error=str(exc))
    finally:
        set_progress(running=False, kind="", phase="")


APPLY_THREAD = None


@app.post("/apply")
def apply():
    global APPLY_THREAD
    if LECTURE_SEULE:
        return redirect(url_for("index", m="Mode lecture seule : aucune photo n'a été corrigée."))
    jobs = []
    for item_id in request.form.getlist("sel"):
        item = ITEMS.get(item_id)
        if not item or (item.get("result") or {}).get("ok"):
            continue
        raw = request.form.get(f"date_{item_id}", "")
        nd = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                nd = dt.datetime.strptime(raw, fmt)
                break
            except ValueError:
                pass
        jobs.append((item, nd))
    with LOCK:
        if STATE["running"] or not jobs:
            return redirect(url_for("index"))
        STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                     total=len(jobs), steps=[], total_estime=False)
    APPLY_THREAD = threading.Thread(target=apply_worker, args=(jobs,), daemon=True)
    APPLY_THREAD.start()
    return redirect(url_for("index"))


@app.get("/progress")
def progress():
    with LOCK:
        st = {k: STATE.get(k) for k in ("running", "kind", "phase", "progress", "done", "total", "started",
                                        "error", "total_estime")}
        steps = [dict(x) for x in STATE.get("steps") or []]
    remaining = None
    if st["running"] and st["started"] and st["total"] and 0 < st["done"] < st["total"]:
        elapsed = now_ts() - st["started"]
        if elapsed > 2:   # trop tôt, l'estimation n'aurait pas de sens
            remaining = int(elapsed / st["done"] * (st["total"] - st["done"]))
    st["remaining"] = remaining
    # temps restant total : étape en cours + étapes à venir estimées
    total_rest, inconnu = remaining or 0, remaining is None and st["total"] != st["done"]
    for x in steps:
        x.pop("started", None)
        if x["status"] == "current":
            x["remaining"] = remaining
            if remaining is None:
                x["remaining"] = x.get("estimate")
                total_rest = x.get("estimate") or 0
                inconnu = x.get("estimate") is None
        elif x["status"] == "todo":
            if x.get("estimate") is None:
                inconnu = True
            else:
                total_rest += x["estimate"]
    st["steps"] = steps
    st["total_remaining"] = None if (inconnu or not steps) else int(total_rest)
    return st


REALIGNED_FILE = DATA_DIR / "videos-realignees.json"
RE_RELU_CREATION = re.compile(r"^relu .* \(CreationDate\)$")


def videos_a_realigner():
    """Vidéos modifiées à tort par les versions < 1.10 : passées pour « sans date » à cause du mode rapide,
    elles ont reçu une date dans les champs QuickTime alors que leur CreationDate (prioritaire) était là.
    Repérées dans corrections.csv (échec « relu … (CreationDate) »), hors celles déjà réalignées."""
    if not LOG_FILE.exists():
        return []
    try:
        faits = set(json.loads(REALIGNED_FILE.read_text(encoding="utf-8")))
    except Exception:
        faits = set()
    liste = []
    with LOG_FILE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            p = row.get("fichier", "")
            if row.get("statut") == "échec" and RE_RELU_CREATION.match(row.get("detail", "")) \
                    and p not in faits and p not in liste and Path(p).exists():
                liste.append(p)
    return liste


def dates_avec_fuseau(path, tags):
    """Dates lues avec leur fuseau horaire (datetime « aware ») : une CreationDate en heure de New York
    et une CreateDate en heure de Paris peuvent désigner le même instant."""
    cmd = ["exiftool", "-j", "-q", "-q", "-api", "QuickTimeUTC", "-d", "%Y-%m-%d %H:%M:%S%z",
           *[f"-{t}" for t in tags], str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        rec = json.loads(p.stdout)[0]
    except Exception:
        return {}
    out = {}
    for t in tags:
        v = rec.get(t)
        if not isinstance(v, str):
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                d = dt.datetime.strptime(v, fmt)
                out[t] = d if d.tzinfo else d.replace(tzinfo=TZ)
                break
            except ValueError:
                pass
    return out


def realign_worker(paths):
    exiger_ecriture()
    faits = []
    try:
        faits = json.loads(REALIGNED_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    ok_n, ko = 0, []
    try:
        set_progress(phase="Réalignement des vidéos sur leur CreationDate", total=len(paths), done=0,
                     started=now_ts(), steps=[])
        for n, p in enumerate(paths, 1):
            set_progress(progress=Path(p).name)
            tags = ["CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate", "MediaCreateDate", "MediaModifyDate"]
            args = ["exiftool", "-overwrite_original", "-q", "-m", "-api", "QuickTimeUTC",
                    *[f"-QuickTime:{t}<CreationDate" for t in tags], "-FileModifyDate<CreationDate", p]
            r = subprocess.run(args, capture_output=True, text=True)
            d = dates_avec_fuseau(p, ["CreationDate", "CreateDate"])
            a, b = d.get("CreationDate"), d.get("CreateDate")
            if r.returncode == 0 and a and b and abs((a - b).total_seconds()) <= 60:
                ok_n += 1
                faits.append(p)
                with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f, delimiter=";").writerow([dt.datetime.now().isoformat(timespec="seconds"), p, "",
                                                           "CreationDate", a.strftime("%d/%m/%Y %H:%M:%S"), "ok",
                                                           "réaligné sur CreationDate"])
            else:
                ko.append(Path(p).name)
            set_progress(done=n)
        REALIGNED_FILE.write_text(json.dumps(faits, ensure_ascii=False), encoding="utf-8")
        msg = f"{ok_n} vidéo(s) réalignée(s) sur leur date d'origine (CreationDate)."
        if ko:
            msg += f" {len(ko)} non réalignée(s) : " + ", ".join(ko[:10]) + ("…" if len(ko) > 10 else "")
        set_progress(messages=[msg + " Lance « Scanner les nouveaux » pour mettre la liste à jour."])
    except Exception as exc:
        set_progress(error=str(exc))
    finally:
        set_progress(running=False, kind="", phase="")


@app.post("/realigner")
def realigner():
    if LECTURE_SEULE:
        return redirect(url_for("index", m="Mode lecture seule : aucune vidéo n'a été modifiée."))
    paths = videos_a_realigner()
    with LOCK:
        if STATE["running"] or not paths:
            return redirect(url_for("index"))
        STATE.update(running=True, kind="correction", error=None, messages=[], progress="", done=0,
                     total=len(paths), steps=[], total_estime=False)
    threading.Thread(target=realign_worker, args=(paths,), daemon=True).start()
    return redirect(url_for("index"))


@app.post("/valider")
def valider():
    """Le dossier est mal daté mais les dates des photos sont bonnes : rien n'est modifié,
    les photos ne sont plus signalées (tant que le fichier ne change pas)."""
    folder = (request.get_json(silent=True) or {}).get("folder", "")
    n = 0
    with LOCK:
        if STATE["running"]:
            return {"ok": False, "msg": "une tâche est en cours"}, 409
        for it in STATE["items"]:
            if it["folder"] != folder or it["status"] in ("abîmé", "sans date") or (it.get("result") or {}).get("ok"):
                continue
            sig = file_sig(Path(it["path"]))
            if not sig:
                continue
            VALIDATED[it["path"]] = sig
            date = it["date"] or "—"
            CORRECTED[it["path"]] = {
                "id": it["id"], "path": it["path"], "name": it["name"], "folder": it["folder"],
                "kind": it["kind"], "immich_id": it.get("immich_id"), "avant": date, "source": it.get("source", ""),
                "apres": date, "note": "dates validées telles quelles (fichier non modifié)",
                "quand": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
            it["result"] = {"ok": True, "msg": "validée sans modification"}
            n += 1
    save_validated()
    save_corrected()
    save_results()
    return {"ok": True, "n": n}


@app.post("/renommer")
def renommer():
    """Renomme (déplace) un dossier de la Pellicule, puis relance une analyse des nouveaux fichiers."""
    if LECTURE_SEULE:
        return {"ok": False, "msg": "mode lecture seule : le dossier n'a pas été renommé"}, 409
    data = request.get_json(silent=True) or {}
    folder, nouveau = data.get("folder", ""), (data.get("nouveau") or "").strip().strip("/")
    src = (SCAN_DIR / folder).resolve()
    dst = (SCAN_DIR / nouveau).resolve()
    racine = SCAN_DIR.resolve()
    if not nouveau or ".." in Path(nouveau).parts or racine not in dst.parents or racine not in src.parents:
        return {"ok": False, "msg": "chemin invalide"}, 400
    if not src.is_dir():
        return {"ok": False, "msg": "dossier introuvable"}, 404
    if dst.exists():
        return {"ok": False, "msg": f"« {nouveau} » existe déjà"}, 409
    if src in dst.parents:
        return {"ok": False, "msg": "impossible de déplacer un dossier dans lui-même"}, 400
    with LOCK:
        if STATE["running"]:
            return {"ok": False, "msg": "une tâche est en cours"}, 409
        STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Renommage du dossier…",
                     progress="", done=0, total=0, started=None, steps=[], total_estime=False)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dst)
        except OSError:
            shutil.move(str(src), str(dst))
    except Exception as exc:
        set_progress(running=False, kind="", phase="", error=f"renommage impossible : {exc}")
        return {"ok": False, "msg": str(exc)}, 500
    ancien, neuf = str(SCAN_DIR / folder), str(SCAN_DIR / nouveau)
    with LOCK:   # les photos corrigées ou validées suivent le dossier
        for store in (CORRECTED, VALIDATED):
            for p in [p for p in store if p == ancien or p.startswith(ancien + os.sep)]:
                rec = store.pop(p)
                q = neuf + p[len(ancien):]
                if isinstance(rec, dict):
                    rec = dict(rec, path=q, folder=str(Path(q).parent.relative_to(SCAN_DIR)), name=Path(q).name)
                store[q] = rec
    save_corrected()
    save_validated()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerow([dt.datetime.now().isoformat(timespec="seconds"), ancien, "", "dossier",
                                               neuf, "ok", "dossier renommé"])

    def suite():
        scan_worker("nouveaux")
        with LOCK:
            STATE["messages"] = [f"Dossier renommé : « {folder} » → « {nouveau} ». Immich verra ces photos "
                                 f"comme de nouveaux fichiers au prochain scan de la bibliothèque."] + STATE["messages"]
    threading.Thread(target=suite, daemon=True).start()
    return {"ok": True}


@app.get("/thumb/<item_id>")
def thumb(item_id):
    item = ITEMS.get(item_id) or next((r for r in list(CORRECTED.values()) if r["id"] == item_id), None)
    if item is None:
        abort(404)
    cache = THUMBS / f"{item_id}.jpg"
    if not cache.exists():
        make_thumb(item, cache)
    if cache.exists() and cache.stat().st_size > 0:
        return send_file(cache, mimetype="image/jpeg", max_age=0)
    return Response(PLACEHOLDER, mimetype="image/svg+xml")


@app.get("/journal.csv")
def journal():
    if not LOG_FILE.exists():
        abort(404)
    return send_file(LOG_FILE, mimetype="text/csv", as_attachment=True, download_name="corrections.csv")


if __name__ == "__main__":
    load_results()
    load_corrected()
    load_validated()
    if not PASSWORD:
        raise SystemExit("Variable VERIF_PASSWORD absente : à définir dans la stack (même valeur que CARNET_PASSWORD).")
    if not SCAN_DIR.is_dir():
        print(f"ATTENTION : dossier introuvable {SCAN_DIR}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
