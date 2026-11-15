"""Immich (facultatif) : index des fichiers, relecture des métadonnées, miniatures."""
import io
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageOps

from . import reglages as R
from . import etat as E

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


def immich_headers():
    return {"x-api-key": R.IMMICH_API_KEY, "Accept": "application/json"}


def immich_enabled() -> bool:
    return bool(R.IMMICH_URL and R.IMMICH_API_KEY and R.IMMICH_ROOT)


def to_immich_path(local_path: Path) -> str:
    return f"{R.IMMICH_ROOT}/{local_path.relative_to(R.LOCAL_ROOT).as_posix()}"


def immich_asset_count():
    """Nombre total de photos et vidéos dans Immich (sert à estimer la lecture de l'index)."""
    try:
        r = requests.get(f"{R.IMMICH_URL}/api/assets/statistics", headers=immich_headers(), timeout=30)
        r.raise_for_status()
        return int(r.json().get("total") or 0) or None
    except Exception:
        return None


def immich_index(meta=None):
    if not immich_enabled():
        return {}, "API Immich non configurée : miniatures locales, pas de date Immich.", 0
    meta = meta or {}
    total = immich_asset_count()
    estime = total is None and bool(meta.get("index_total"))
    total = total or meta.get("index_total") or 0
    E.step_start("index", total=total, total_estime=estime)
    idx, page, vus = {}, 1, 0
    try:
        while page:
            r = requests.post(f"{R.IMMICH_URL}/api/search/metadata", headers=immich_headers(),
                              json={"page": page, "size": 1000}, timeout=120)
            r.raise_for_status()
            assets = r.json().get("assets", {})
            for it in assets.get("items", []):
                vus += 1
                op = it.get("originalPath", "")
                if op.startswith(R.IMMICH_ROOT + "/"):
                    idx[op] = (it.get("id"), it.get("localDateTime"))
            nxt = assets.get("nextPage")
            page = int(nxt) if nxt else None
            with E.LOCK:
                if E.STATE["total"] and vus > E.STATE["total"]:
                    E.STATE["total"] = vus
                E.STATE.update(done=vus, progress=f"{len(idx)} fichiers de la Pellicule reconnus dans Immich")
        return idx, f"Immich : {len(idx)} fichiers de la photothèque indexés.", vus
    except Exception as exc:
        return idx, f"Immich injoignable ou API refusée ({exc}). Analyse faite sur les fichiers seuls.", vus


def immich_refresh(asset_ids):
    if not (immich_enabled() and asset_ids):
        return ""
    try:
        r = requests.post(f"{R.IMMICH_URL}/api/assets/jobs", headers=immich_headers(),
                          json={"assetIds": asset_ids, "name": "refresh-metadata"}, timeout=60)
        r.raise_for_status()
        return f"Immich : relecture des métadonnées demandée pour {len(asset_ids)} fichier(s)."
    except Exception as exc:
        return f"Immich : relecture non demandée ({exc}). Lance un scan de la bibliothèque externe."


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
            r = requests.get(f"{R.IMMICH_URL}/api/assets/{item['immich_id']}/thumbnail",
                             headers=immich_headers(), params={"size": "thumbnail"}, timeout=30)
            r.raise_for_status()
            save_thumb(Image.open(io.BytesIO(r.content)), cache)
            return
        except Exception:
            pass
    try:
        if ext in R.VIDEO_EXT:
            for ss in ("1", "0"):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", ss, "-i", str(path),
                                "-frames:v", "1", "-vf", "scale=320:-2", str(cache)],
                               capture_output=True, timeout=60)
                if cache.exists() and cache.stat().st_size > 0:
                    return
        elif ext in R.RAW_EXT:
            for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
                p = subprocess.run(["exiftool", "-b", tag, str(path)], capture_output=True, timeout=60)
                if p.stdout:
                    save_thumb(Image.open(io.BytesIO(p.stdout)), cache)
                    return
        else:
            save_thumb(Image.open(path), cache)
    except Exception:
        pass
