"""Éphéméride de démonstration, pour les tests d'écran : une petite Pellicule créée
pour l'occasion (vrais fichiers, exiftool réel), déjà analysée. Il faut l'interface compilée (cd frontend && npx vite build).

    python tests/interface/serveur.py <dossier> <port>

Mot de passe : « essai-ephemeride »."""
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

dossier, port = Path(sys.argv[1]), int(sys.argv[2])
os.environ.update({"LOCAL_ROOT": str(dossier / "photos"), "DATA_DIR": str(dossier / "data"), "TZ": "Europe/Paris",
                   "MAISON_PASSWORD": "essai-ephemeride", "PORT": str(port)})
for k in ("IMMICH_URL", "IMMICH_API_KEY", "IMMICH_ROOT", "VERIF_LECTURE_SEULE", "VERIF_PASSWORD", "MAISON_LECTURE_SEULE",
          "MAISON_JETON", "SCAN_SUBDIR", "TOLERANCE_DAYS"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outil import charger  # noqa: E402

outil = charger(recharger=False)


def photo(chemin: Path, date: str | None, couleur: str) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), couleur).save(chemin)
    if date:
        subprocess.run(["exiftool", "-q", "-overwrite_original", f"-DateTimeOriginal={date}", str(chemin)], check=True)


P = outil.SCAN_DIR
mariage = P / "2019" / "2019-07-14 Mariage"
for i in range(4):
    photo(mariage / f"IMG_20190714_15{i}000.jpg", "2019:07:14 15:%d0:00" % i, "#8aa2c8")
photo(mariage / "IMG_20190714_161500.jpg", "2021:03:05 10:11:12", "#c88a8a")          # écart, le nom donne la date
photo(mariage / "sans-date.jpg", None, "#9fb89a")                                       # sans date EXIF
photo(P / "2008" / "2008-08-10 Plage" / "IMG_20110502_101010.jpg", "2011:05:02 10:10:10", "#c8b98a")   # à trancher
photo(P / "2008" / "2008-08-10 Plage" / "a.jpg", "2008:08:10 09:00:00", "#8ac8c0")
photo(P / "2008" / "2008-08-10 Plage" / "b.jpg", "2008:08:10 09:30:00", "#8ac8c0")
photo(P / "2008" / "2008-08-10 Plage" / "c.jpg", "2012:01:01 08:00:00", "#b08ac8")    # écart
outil.scan_worker()


def anneau_de_demo(adresse, jeton):
    """3.2.0 : faux Anneau pour « Restaurer les réglages » (aucun réseau)."""
    if "?le=" not in adresse:
        return {"dates": ["2026-11-15"]}
    return {"le": "2026-11-15", "version": "3.2.0", "reglages": {"sous_dossier": "Pellicule", "tolerance_jours": 2}}


outil.ETAT["lire_sauvegarde"] = anneau_de_demo
(dossier / "pret").write_text("ok")
import uvicorn  # noqa: E402
uvicorn.run(outil.app, host="127.0.0.1", port=port, log_level="warning")
