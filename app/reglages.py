"""Réglages lus dans l'environnement du conteneur, chemins des fichiers d'état et constantes."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo


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
CACHE_FILE = DATA_DIR / "cache.json"   # résultat par fichier (taille + date de modification), mode incrémental
META_FILE = DATA_DIR / "mesures.json"   # durées et volumes de la dernière analyse, pour les estimations


DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBS.mkdir(parents=True, exist_ok=True)


PHOTO_EXT = {"jpg", "jpeg", "heic", "heif", "png", "webp", "gif", "tif", "tiff"}
VIDEO_EXT = {"mov", "mp4", "m4v", "3gp", "avi", "mkv", "mts", "m2ts"}
RAW_EXT = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "pef", "srw"}
ALL_EXT = PHOTO_EXT | VIDEO_EXT | RAW_EXT
NEEDS_MP4 = {"avi", "mts", "m2ts", "mkv"}   # exiftool ne sait pas y écrire de date
QUICKTIME_EXT = {"mov", "mp4", "m4v", "3gp"}
READ_TAGS = ["-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-FileModifyDate",
             "-FileTypeExtension", "-Error", "-Warning"]


# extensions équivalentes : pas d'alerte si le contenu est de l'une et le nom de l'autre
EXT_EQUIV = [{"jpg", "jpeg"}, {"tif", "tiff"}, {"heic", "heif"}, {"mts", "m2ts"},
             {"mov", "mp4", "m4v", "3gp", "qt"}]
CLEANED_DIR = DATA_DIR / "originaux-nettoyes"   # JPEG d'origine avant retrait des octets parasites
DOUBLONS_DIR = DATA_DIR / "doublons"   # fichiers mal nommés identiques à un fichier déjà présent
DATE_PRIORITY = ["DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate"]
FMT = "%Y-%m-%d %H:%M:%S"
VALIDATED_FILE = DATA_DIR / "validees.json"
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot"}
CHUNK = 100   # fichiers par appel exiftool : assez gros pour être rapide, assez petit pour la barre
REALIGNED_FILE = DATA_DIR / "videos-realignees.json"
