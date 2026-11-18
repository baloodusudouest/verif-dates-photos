"""Réglages : variables du conteneur (chemins, Immich, lecture seule), réglages d'usage (écran Réglages),
chemins des fichiers d'état et constantes.

3.2.0 : le sous-dossier analysé et la tolérance ne se lisent plus dans la stack. Ils sont gardés dans
<DATA_DIR>/reglages.json (module maison, classe Reglages) et se changent dans l'écran Réglages, sans redémarrer.
Au premier démarrage, ils sont repris une fois des anciennes variables SCAN_SUBDIR et TOLERANCE_DAYS si
elles sont encore posées ; ensuite ces variables ne sont plus jamais lues."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from . import maison


LOCAL_ROOT = Path(os.environ.get("LOCAL_ROOT", "/photos"))
IMMICH_URL = os.environ.get("IMMICH_URL", "").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ROOT = os.environ.get("IMMICH_ROOT", "").rstrip("/")  # dossier Photos tel que vu par Immich
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Paris"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PORT = int(os.environ.get("PORT", "8099"))
ORIGINALS_DIR = os.environ.get("ORIGINALS_DIR", "").strip()   # vide : originaux convertis supprimés


# 1.11.0 : mot de passe commun aux applis (obligatoire) et mode lecture seule
# 3.1.0 : variables communes de la maison (module maison).
# 3.2.0 : l'ancien nom du mot de passe n'est plus lu : MAISON_PASSWORD seul, 12 caractères au moins, sinon
#   l'outil refuse de démarrer. La lecture seule garde son forçage propre à l'appli :
#   VERIF_LECTURE_SEULE si elle dit oui ou non (elle l'emporte), sinon MAISON_LECTURE_SEULE.
MOT_DE_PASSE_MIN = 12
SANS_MOT_DE_PASSE = (f"MAISON_PASSWORD est absent ou trop court ({MOT_DE_PASSE_MIN} caractères au moins) : "
                     "Éphéméride ne démarre pas.")
PASSWORD = maison.mot_de_passe()
if len(PASSWORD) < MOT_DE_PASSE_MIN:
    raise SystemExit(SANS_MOT_DE_PASSE)
LECTURE_SEULE = maison.lecture_seule("VERIF_LECTURE_SEULE")
ORIGINE_LECTURE_SEULE = maison.origine_lecture_seule("VERIF_LECTURE_SEULE")   # « appli », « maison » ou « »
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


# ---------------------------------------------------------------- réglages d'usage (3.2.0, écran Réglages)
# clé : (valeur par défaut, ancienne variable de la stack reprise une seule fois au premier démarrage)
APPLI_ID = "verif-dates-photos"           # nom du dépôt : identifiant de l'appli dans les sauvegardes de l'Anneau
DEFAUTS = {"sous_dossier": "Pellicule", "tolerance_jours": 1}
ANCIENNES_VARIABLES = {"sous_dossier": "SCAN_SUBDIR", "tolerance_jours": "TOLERANCE_DAYS"}
TOLERANCE_MAX = 366
# Clés qui ne sortent jamais dans une sauvegarde : les renseignements fixés par la stack, montrés dans Réglages
# mais qui ne se règlent pas ici (et ne doivent pas se restaurer).
EXCLUS_SAUVEGARDE = ("lecture_seule", "lecture_seule_origine", "originaux_gardes", "dossier_present")
REGLAGES_FILE = DATA_DIR / "reglages.json"
USAGE = maison.Reglages(REGLAGES_FILE, DEFAUTS, ANCIENNES_VARIABLES)


def verifier(valeurs: dict) -> dict:
    """Contrôle des valeurs reçues de l'écran ou d'une sauvegarde ; ValueError avec un message clair sinon.
    Rend les valeurs propres (clés connues seulement)."""
    if not isinstance(valeurs, dict):
        raise ValueError("Réglages attendus sous la forme {clé: valeur}.")
    propres = {}
    if "tolerance_jours" in valeurs:
        v = valeurs["tolerance_jours"]
        if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).strip().lstrip("-").isdigit():
            raise ValueError("tolerance_jours : nombre entier de jours attendu.")
        v = int(str(v).strip())
        if not 0 <= v <= TOLERANCE_MAX:
            raise ValueError(f"tolerance_jours : entre 0 et {TOLERANCE_MAX} jours.")
        propres["tolerance_jours"] = v
    if "sous_dossier" in valeurs:
        v = valeurs["sous_dossier"]
        if not isinstance(v, str):
            raise ValueError("sous_dossier : texte attendu (nom du dossier dans le dossier des photos).")
        v = v.strip().strip("/")
        morceaux = v.split("/")
        if v.startswith("~") or "\\" in v or ":" in v or (v and any(m in ("", ".", "..") for m in morceaux)):
            raise ValueError("sous_dossier : un dossier à l'intérieur du dossier des photos, sans « .. » ni chemin complet.")
        propres["sous_dossier"] = v
    return propres


def appliquer() -> None:
    """Recopie les réglages d'usage dans les noms lus par le reste de l'outil (R.SCAN_DIR, R.TOLERANCE_DAYS) :
    un changement prend effet tout de suite, sans redémarrer."""
    global SCAN_DIR, TOLERANCE_DAYS
    valeurs = USAGE.tout()
    SCAN_DIR = LOCAL_ROOT / valeurs["sous_dossier"] if valeurs["sous_dossier"] else LOCAL_ROOT
    TOLERANCE_DAYS = int(valeurs["tolerance_jours"])


def changer(valeurs: dict) -> dict:
    """Vérifie, enregistre et applique ; rend les réglages d'usage."""
    propres = verifier(valeurs)
    tout = USAGE.changer(propres)
    appliquer()
    return tout


SCAN_DIR: Path = LOCAL_ROOT
TOLERANCE_DAYS: int = 1
appliquer()


PHOTO_EXT = {"jpg", "jpeg", "heic", "heif", "png", "webp", "gif", "tif", "tiff"}
VIDEO_EXT = {"mov", "mp4", "m4v", "3gp", "avi", "mkv", "mts", "m2ts"}
RAW_EXT = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "pef", "srw"}
ALL_EXT = PHOTO_EXT | VIDEO_EXT | RAW_EXT
NEEDS_MP4 = {"avi", "mts", "m2ts", "mkv"}   # exiftool ne sait pas y écrire de date
QUICKTIME_EXT = {"mov", "mp4", "m4v", "3gp"}
READ_TAGS = ["-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-FileModifyDate",
             "-FileTypeExtension", "-Error", "-Warning"]


# extensions équivalentes : pas d'alerte si le contenu est de l'une et le nom de l'autre
EXT_EQUIV = [{"jpg", "jpeg"}, {"tif", "tiff"}, {"heic", "heif"}, {"mts", "m2ts", "m2t", "ts"},
             {"mov", "mp4", "m4v", "3gp", "qt"}]
CLEANED_DIR = DATA_DIR / "originaux-nettoyes"   # JPEG d'origine avant retrait des octets parasites
DOUBLONS_DIR = DATA_DIR / "doublons"   # fichiers mal nommés identiques à un fichier déjà présent
# 3.2.5 (audit, M47) : copie de chaque photo avant la première écriture de sa date (une fois par fichier)
AVANT_DATE_DIR = DATA_DIR / "originaux-avant-date"
DATE_PRIORITY = ["DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate"]
FMT = "%Y-%m-%d %H:%M:%S"
VALIDATED_FILE = DATA_DIR / "validees.json"
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot"}
CHUNK = 100   # fichiers par appel exiftool : assez gros pour être rapide, assez petit pour la barre
REALIGNED_FILE = DATA_DIR / "videos-realignees.json"
