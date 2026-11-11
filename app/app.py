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
- Photos corrigées retirées de la liste « À traiter » et rangées dans « Déjà corrigées »
- Barre de progression en direct (index Immich, recensement, analyse fichier par fichier, corrections)
- Affiche une page de vérification avec miniatures (Immich si l'asset est connu, sinon locales)
- Ne corrige QUE les fichiers cochés, puis relit le fichier pour vérifier l'écriture
- Journalise chaque correction dans <DATA_DIR>/corrections.csv
- Demande à Immich de relire les métadonnées des assets corrigés (si l'API est configurée)
"""
__version__ = "1.4.0"

import calendar
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
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for
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

THUMBS = DATA_DIR / "thumbs"
RESULTS_FILE = DATA_DIR / "results.json"
LOG_FILE = DATA_DIR / "corrections.csv"
CORRECTED_FILE = DATA_DIR / "corrigees.json"   # photos corrigées (section « Déjà corrigées »)
VOISINES_MIN = 2         # nombre minimal d'autres photos datées pour juger la cohérence
VOISINES_PART = 0.5      # part minimale du jour dominant pour le proposer
CACHE_FILE = DATA_DIR / "cache.json"   # résultat par fichier (taille + date de modification) pour le mode incrémental
DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBS.mkdir(parents=True, exist_ok=True)

PHOTO_EXT = {"jpg", "jpeg", "heic", "heif", "png", "webp", "gif", "tif", "tiff"}
VIDEO_EXT = {"mov", "mp4", "m4v", "3gp", "avi", "mkv", "mts", "m2ts"}
RAW_EXT = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "pef", "srw"}
ALL_EXT = PHOTO_EXT | VIDEO_EXT | RAW_EXT

READ_TAGS = ["-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-FileModifyDate"]
DATE_PRIORITY = ["DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate"]
FMT = "%Y-%m-%d %H:%M:%S"

app = Flask(__name__)
LOCK = threading.Lock()
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
}
ITEMS = {}
CORRECTED = {}   # chemin → enregistrement de la correction


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
    cmd = ["exiftool", "-j", "-q", "-q", "-fast2", "-api", "QuickTimeUTC", "-d", FMT,
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


def list_files():
    """Recense les fichiers à analyser (ignore les dossiers techniques Synology)."""
    files = []
    for root, dirs, names in os.walk(SCAN_DIR):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for n in sorted(names):
            if not n.startswith(".") and Path(n).suffix.lstrip(".").lower() in ALL_EXT:
                files.append(Path(root) / n)
    return files


def run_exiftool_files(paths):
    """Lit les dates d'une liste de fichiers en un seul appel exiftool (liste passée par fichier -@)."""
    if not paths:
        return []
    argfile = DATA_DIR / "exiftool-args.txt"
    argfile.write_text("\n".join(str(x) for x in paths) + "\n", encoding="utf-8")
    cmd = ["exiftool", "-j", "-q", "-q", "-fast2", "-api", "QuickTimeUTC", "-d", FMT,
           "-charset", "filename=utf8", *READ_TAGS, "-@", str(argfile)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def set_progress(**kw):
    with LOCK:
        STATE.update(**kw)


def pick_date(rec):
    for tag in DATE_PRIORITY:
        d = parse_date(rec.get(tag))
        if d:
            return d, tag
    return None, None


# --------------------------------------------------------------------------- index Immich
def immich_index():
    if not immich_enabled():
        return {}, "API Immich non configurée : miniatures locales, pas de date Immich."
    idx, page = {}, 1
    try:
        while page:
            r = requests.post(f"{IMMICH_URL}/api/search/metadata", headers=immich_headers(),
                              json={"page": page, "size": 1000}, timeout=120)
            r.raise_for_status()
            assets = r.json().get("assets", {})
            for it in assets.get("items", []):
                op = it.get("originalPath", "")
                if op.startswith(IMMICH_ROOT + "/"):
                    idx[op] = (it.get("id"), it.get("localDateTime"))
            nxt = assets.get("nextPage")
            page = int(nxt) if nxt else None
            set_progress(progress=f"{len(idx)} fichiers de la photothèque trouvés (page {page or 'fin'})")
        return idx, f"Immich : {len(idx)} fichiers de la photothèque indexés."
    except Exception as exc:
        return idx, f"Immich injoignable ou API refusée ({exc}). Analyse faite sur les fichiers seuls."


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

    # 1. Le nom du fichier contredit le dossier : toujours à trancher
    if nd and ref and gap_days(ref, nd) > TOLERANCE_DAYS:
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
            if it["status"] != "à trancher" and it["proposed_src"] != "nom du fichier":
                it["proposed"], it["proposed_src"] = it["choice_voisins"], "autres photos du dossier"


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
        stats = {"fichiers": 0, "ok": 0, "ecart": 0, "sans_date": 0, "a_trancher": 0, "sans_ref": 0}
        set_progress(phase="1/3 · Lecture de l'index Immich", progress="", done=0, total=0)
        immich_idx, immich_msg = immich_index()

        set_progress(phase="2/3 · Recensement des fichiers", progress="", immich=immich_msg)
        files = list_files()
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

        libelle = "3/3 · Analyse des dates"
        if mode == "nouveaux":
            libelle += f" — {len(to_scan):,} nouveaux ou modifiés, {reused:,} repris".replace(",", " ")
        set_progress(phase=libelle, total=len(to_scan), done=0, started=dt.datetime.now().timestamp())
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
        for it in items:   # photo déjà corrigée mais toujours en écart : signalée à nouveau
            rec = CORRECTED.get(it["path"])
            it["deja"] = rec["quand"] if rec else ""
        items.sort(key=lambda it: (it["folder"], it["name"]))
        with LOCK:
            STATE.update(items=items, stats=stats, scanned_at=dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
                         progress="Terminé", error=None, last_mode=mode,
                         messages=([f"Analyse incrémentale : {len(to_scan)} fichier(s) relu(s), "
                                    f"{reused} repris de l'analyse précédente."] if mode == "nouveaux" else []))
            ITEMS.clear()
            ITEMS.update({it["id"]: it for it in items})
        save_cache(new_cache)
        save_results()
    except Exception as exc:
        set_progress(error=str(exc))
    finally:
        set_progress(running=False, kind="", phase="")


def save_results():
    with LOCK:
        data = {k: STATE[k] for k in ("items", "stats", "immich", "scanned_at")}
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
def write_date(item, nd: dt.datetime):
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
        if item.get("has_creationdate"):
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
<title>Vérif dates Pellicule</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#222;--mute:#777;--line:#e2e0db;--warn:#c2410c;--bad:#b91c1c;--ok:#15803d;--acc:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#18181b;--card:#242428;--ink:#eee;--mute:#999;--line:#38383d;--acc:#60a5fa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:18px;margin:0 12px 0 0}main{padding:16px;max-width:1400px;margin:auto}
button,.btn{border:0;border-radius:8px;padding:8px 14px;background:var(--acc);color:#fff;font-weight:600;cursor:pointer;text-decoration:none}
button.sec{background:transparent;color:var(--ink);border:1px solid var(--line)}
.stats{color:var(--mute);font-size:13px}.msg{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:14px}
.folder{margin:22px 0 8px;display:flex;gap:10px;align-items:center}.folder h2{font-size:15px;margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:170px;object-fit:cover;background:#ccc}.body{padding:10px;font-size:13px;display:flex;flex-direction:column;gap:4px}
.name{font-weight:600;word-break:break-all}.tag{display:inline-block;border-radius:5px;padding:1px 6px;font-size:12px;font-weight:600;color:#fff}
.t-ecart{background:var(--warn)}.t-sans{background:var(--bad)}.t-trancher{background:#7c3aed}.mute{color:var(--mute)}
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
.scanbtns{display:flex;gap:8px}
select{padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink)}
</style></head><body>
<header>
  <h1>Vérification des dates — Pellicule <span class="mute" style="font-size:12px;font-weight:400">v{{ version }}</span></h1>
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
    <option value="corrigees">Déjà corrigées ({{ corrected|length }})</option>
  </select>
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
  {{ state.stats.ok }} OK · {{ state.stats.ecart }} écarts · {{ state.stats.sans_date }} sans date · {{ state.stats.a_trancher or 0 }} à trancher ·
  {{ state.stats.sans_ref }} hors dossier daté · tolérance {{ tol }} j{% endif %}{% if corrected %} · {{ corrected|length }} corrigée(s){% endif %}</span>
</header>
<main>
<div id="prog" class="msg prog" {% if not state.running %}hidden{% endif %}>
  <div class="prog-head"><strong id="prog-phase">{{ state.phase }}</strong><span id="prog-count" class="mute"></span></div>
  <div class="bar"><div id="prog-fill" class="fill"></div></div>
  <div class="prog-foot"><span id="prog-detail" class="mute"></span><span id="prog-eta" class="mute"></span></div>
</div>
{% if state.error %}<div class="msg res-ko">Erreur : {{ state.error }}</div>{% endif %}
{% if state.immich %}<div class="msg mute">{{ state.immich }}</div>{% endif %}
{% for m in messages %}<div class="msg">{{ m }}</div>{% endfor %}
{% if not pending and state.scanned_at and not state.running %}<div class="msg res-ok" id="rien">Aucune photo à traiter.</div>{% endif %}
<form id="f" method="post" action="{{ url_for('apply') }}">
{% for folder, group in groups %}
  <div class="folder" data-folder><h2>{{ folder }}</h2><span class="mute">dossier : {{ group[0].ref }}</span>
  <button class="sec" type="button" onclick="cocherDossier(this)">Cocher le dossier</button></div>
  <div class="grid">
  {% for it in group %}
    <div class="card" data-status="{{ it.status }}"{% if it.voisins %} data-vmin="{{ it.voisins.min }}" data-vmax="{{ it.voisins.max }}"{% endif %}>
      <img loading="lazy" src="{{ url_for('thumb', item_id=it.id) }}" alt="">
      <div class="body">
        <div class="name">{{ it.name }}</div>
        <div><span class="tag {{ 't-sans' if it.status == 'sans date' else ('t-trancher' if it.status == 'à trancher' else 't-ecart') }}">{{ it.status }}</span>
          <span class="mute">{{ it.kind }}{% if it.gap %} · {{ it.gap }} j d'écart{% endif %}</span></div>
        <div>Fichier : {{ it.date or '—' }} {% if it.source %}<span class="mute">({{ it.source }})</span>{% endif %}</div>
        {% if it.name_date %}<div>Nom du fichier : {{ it.name_date }}</div>{% endif %}
        {% if it.immich_date %}<div class="mute">Immich : {{ it.immich_date }}</div>{% endif %}
        <div class="mute">Voisines : {{ it.voisins.label if it.voisins else "pas assez d'autres photos datées dans le dossier" }}</div>
        {% if it.deja %}<div class="res-ko">Déjà corrigée le {{ it.deja }}, écart toujours présent</div>{% endif %}
        {% if it.result and not it.result.ok %}<div class="res-ko">✗ échec — {{ it.result.msg }}</div>{% endif %}
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
      </div>
    </div>
  {% endfor %}
  </div>
{% endfor %}
</section>
</main>
<script>
function visibles(){return [...document.querySelectorAll('.card')].filter(c=>c.style.display!=='none')}
function cocher(v){visibles().forEach(c=>{const b=c.querySelector('input[type=checkbox]');if(b)b.checked=v})}
function cocherDossier(btn){let g=btn.parentElement.nextElementSibling;g.querySelectorAll('.card').forEach(c=>{
  if(c.style.display==='none')return;const b=c.querySelector('input[type=checkbox]');if(b)b.checked=true})}
function filtrer(){const f=document.getElementById('filtre').value,corr=f==='corrigees';
  document.getElementById('corrigees').hidden=!corr;document.getElementById('f').hidden=corr;
  const rien=document.getElementById('rien');if(rien)rien.hidden=corr;
  document.querySelectorAll('.actions').forEach(a=>a.style.display=corr?'none':'');
  document.querySelectorAll('#f .card').forEach(c=>{
    let show=f==='tous'||(f==='incoherent'?c.dataset.coh==='0':c.dataset.status===f);c.style.display=show?'':'none'});
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
function duree(s){if(s==null)return'';if(s<60)return'≈ '+s+' s restantes';
  const m=Math.round(s/60);return m<60?'≈ '+m+' min restantes':'≈ '+Math.floor(m/60)+' h '+String(m%60).padStart(2,'0')+' restantes'}
let wasRunning={{ 'true' if state.running else 'false' }};
async function suivre(){
  let st;try{st=await (await fetch('{{ url_for('progress') }}',{cache:'no-store'})).json()}catch(e){return setTimeout(suivre,3000)}
  const box=document.getElementById('prog');
  if(st.running){
    wasRunning=true;box.hidden=false;
    document.getElementById('prog-phase').textContent=st.phase||'…';
    const bar=box.querySelector('.bar'),fill=document.getElementById('prog-fill');
    if(st.total>0){bar.classList.remove('indet');const pc=Math.floor(st.done*100/st.total);fill.style.width=pc+'%';
      document.getElementById('prog-count').textContent=st.done.toLocaleString('fr-FR')+' / '+st.total.toLocaleString('fr-FR')+' · '+pc+' %';}
    else{bar.classList.add('indet');fill.style.width='';document.getElementById('prog-count').textContent='';}
    document.getElementById('prog-detail').textContent=st.progress||'';
    document.getElementById('prog-eta').textContent=duree(st.remaining);
    setTimeout(suivre,1500);
  } else if(wasRunning){location.replace('{{ url_for('index') }}')}
}
if(wasRunning)suivre();
function confirmer(){
  const vides=[...document.querySelectorAll('input[name=sel]:checked')].filter(b=>!b.closest('.body').querySelector('input[type=datetime-local]').value).length;
  if(vides){alert(vides+' photo(s) à trancher cochée(s) sans date choisie.');return false}const n=document.querySelectorAll('input[name=sel]:checked').length;
  if(!n){alert('Aucun fichier coché.');return false}
  const ko=[...document.querySelectorAll('input[name=sel]:checked')].filter(b=>b.closest('.card').dataset.coh==='0').length;
  if(ko&&!confirm(ko+' photo(s) cochée(s) ont une date hors de la période des autres photos du dossier. Continuer quand même ?'))return false;
  return confirm(n+' fichier(s) vont être modifiés directement sur le NAS (sans copie _original). Continuer ?')}
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
    counts = {k: sum(1 for it in pending if it["status"] == k) for k in ("écart", "sans date", "à trancher")}
    messages = request.args.getlist("m") + list(state.get("messages") or [])
    return render_template_string(PAGE, state=state, pending=pending, groups=list(groups.items()),
                                  corrected=corrected, cgroups=list(cgroups.items()), counts=counts,
                                  tol=TOLERANCE_DAYS, messages=messages, version=__version__,
                                  has_cache=CACHE_FILE.exists())


@app.post("/scan")
def scan():
    with LOCK:
        if STATE["running"]:
            return redirect(url_for("index"))
        STATE.update(running=True, kind="analyse", error=None, messages=[], phase="Démarrage…",
                     progress="", done=0, total=0, started=None)
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
                ok, msg = write_date(item, nd)
                if ok:
                    ok, msg = verify(item, nd)
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
                     total=len(jobs))
    APPLY_THREAD = threading.Thread(target=apply_worker, args=(jobs,), daemon=True)
    APPLY_THREAD.start()
    return redirect(url_for("index"))


@app.get("/progress")
def progress():
    with LOCK:
        st = {k: STATE[k] for k in ("running", "kind", "phase", "progress", "done", "total", "started", "error")}
    remaining = None
    if st["running"] and st["started"] and st["total"] and 0 < st["done"] < st["total"]:
        elapsed = dt.datetime.now().timestamp() - st["started"]
        remaining = int(elapsed / st["done"] * (st["total"] - st["done"]))
    st["remaining"] = remaining
    return st


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
    if not SCAN_DIR.is_dir():
        print(f"ATTENTION : dossier introuvable {SCAN_DIR}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
