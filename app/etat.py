"""État en mémoire (analyse, corrigées, validées), verrous, fichiers d'état, journal et progression."""
import csv
import datetime as dt
import json
import threading
from pathlib import Path

from . import __version__
from . import reglages as R


LOCK = threading.Lock()      # état en mémoire (STATE, ITEMS, CORRECTED, VALIDATED)


ECRITURE = threading.RLock()  # fichiers d'état et journal : une seule écriture à la fois


def ecrire_json(path: Path, texte: str):
    """Écrit un fichier d'état sans risque de le laisser à moitié écrit : fichier temporaire propre
    au fil d'exécution, puis remplacement d'un coup. Une seule écriture à la fois (verrou ECRITURE)."""
    with ECRITURE:
        tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
        tmp.write_text(texte, encoding="utf-8")
        tmp.replace(path)


def noter_journal(ligne):
    """Ajoute une ligne à corrections.csv (en-tête si le fichier est neuf), une écriture à la fois."""
    with ECRITURE:
        neuf = not R.LOG_FILE.exists()
        with R.LOG_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            if neuf:
                w.writerow(["horodatage", "fichier", "ancienne_date", "source", "nouvelle_date", "statut", "detail"])
            w.writerow(ligne)


class LectureSeule(RuntimeError):
    """Écriture refusée : l'outil tourne en lecture seule (VERIF_LECTURE_SEULE, ou MAISON_LECTURE_SEULE depuis la 3.1.0)."""


def exiger_ecriture():
    """À appeler en tête de tout ce qui modifie, déplace ou supprime un fichier de la Pellicule."""
    if R.LECTURE_SEULE:
        raise LectureSeule("Mode lecture seule : aucun fichier n'est modifié.")


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


def load_meta():
    try:
        return json.loads(R.META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_meta(meta):
    try:
        ecrire_json(R.META_FILE, json.dumps(meta))
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


def set_progress(**kw):
    with LOCK:
        STATE.update(**kw)


def load_validated():
    try:
        VALIDATED.update(json.loads(R.VALIDATED_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass


def save_validated():
    with LOCK:   # copie figée : le dictionnaire peut changer pendant l'écriture
        texte = json.dumps(VALIDATED, ensure_ascii=False)
    ecrire_json(R.VALIDATED_FILE, texte)


def load_corrected():
    try:
        CORRECTED.update(json.loads(R.CORRECTED_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass


def save_corrected():
    with LOCK:
        texte = json.dumps(CORRECTED, ensure_ascii=False)
    ecrire_json(R.CORRECTED_FILE, texte)


def cache_params():
    """Réglages qui changent le résultat : s'ils changent, le cache n'est plus valable."""
    return {"version": __version__.rsplit(".", 1)[0], "tol": R.TOLERANCE_DAYS, "root": str(R.SCAN_DIR),
            "immich": R.IMMICH_ROOT}


def load_cache():
    try:
        data = json.loads(R.CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("params") == cache_params():
            return data.get("files", {})
    except Exception:
        pass
    return {}


def save_cache(files):
    ecrire_json(R.CACHE_FILE, json.dumps({"params": cache_params(), "files": files}, ensure_ascii=False))


def save_results():
    with LOCK:
        texte = json.dumps({k: STATE.get(k) for k in ("items", "stats", "immich", "scanned_at", "folders")},
                           ensure_ascii=False)
    ecrire_json(R.RESULTS_FILE, texte)


def load_results():
    if R.RESULTS_FILE.exists():
        try:
            data = json.loads(R.RESULTS_FILE.read_text(encoding="utf-8"))
            STATE.update(data)
            ITEMS.update({i["id"]: i for i in STATE["items"]})
        except Exception:
            pass


def log_correction(item, nd, ok, msg):
    noter_journal([dt.datetime.now().isoformat(timespec="seconds"), item["path"], item["date"],
             item["source"], nd.strftime("%d/%m/%Y %H:%M:%S"), "ok" if ok else "échec", msg])
