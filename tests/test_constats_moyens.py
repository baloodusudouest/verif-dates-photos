"""Constats moyens de l'audit du 17/11/2026 corrigés en 3.2.5 : délais d'exiftool (M46), copie de la photo
avant la première écriture de date (M47), état écrit sous le verrou de sa photographie (M48)."""
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dates import app  # noqa: E402,F401 — fixture commune

exiftool = pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
ND = dt.datetime(2008, 8, 10, 1, 41, 36)


def _photo(app, nom="2008-08-10-014136052.jpg"):
    dossier = app.SCAN_DIR / "2008" / "2008 - Vacances inventées"
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    Image.new("RGB", (30, 30), "blue").save(chemin)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2022:12:24 14:42:39", str(chemin)],
                   check=True)
    return chemin


# ---------------------------------------------------------------- M46 : délais d'exiftool

@exiftool
def test_une_ecriture_trop_longue_est_arretee_et_dite(app, monkeypatch):
    chemin = _photo(app)
    monkeypatch.setattr(app.module_de("DELAI_ECRITURE"), "DELAI_ECRITURE", 0.001)
    ok, msg = app.write_date({"path": str(chemin)}, ND)
    assert ok is False and "n'a pas fini" in msg


@exiftool
def test_une_relecture_trop_longue_est_un_echec_clair(app, monkeypatch):
    chemin = _photo(app)
    monkeypatch.setattr(app.module_de("DELAI_LECTURE"), "DELAI_LECTURE", 0.001)
    ok, msg = app.verify({"path": str(chemin)}, ND)
    assert ok is False and "n'a pas fini" in msg


@exiftool
def test_un_lot_trop_long_nomme_le_fichier_qui_bloque(app, monkeypatch):
    chemins = [_photo(app, "IMG_20080810_014136.jpg"), _photo(app)]
    monkeypatch.setattr(app.module_de("DELAI_LOT"), "DELAI_LOT", 0.001)
    monkeypatch.setattr(app.module_de("DELAI_LECTURE"), "DELAI_LECTURE", 0.001)
    with pytest.raises(RuntimeError, match="ne finit pas de lire IMG_20080810_014136.jpg"):
        app.run_exiftool_files(chemins)


@exiftool
def test_l_analyse_bloquee_s_arrete_avec_une_erreur(app, monkeypatch):
    _photo(app)
    monkeypatch.setattr(app.module_de("DELAI_LOT"), "DELAI_LOT", 0.001)
    monkeypatch.setattr(app.module_de("DELAI_LECTURE"), "DELAI_LECTURE", 0.001)
    app.scan_worker()
    assert "ne finit pas de lire" in (app.STATE["error"] or "")
    assert app.STATE["running"] is False


# ---------------------------------------------------------------- M47 : copie avant la première écriture

@exiftool
def test_la_photo_est_copiee_avant_la_premiere_ecriture_de_date(app):
    chemin = _photo(app)
    avant = chemin.read_bytes()
    ok, msg = app.write_date({"path": str(chemin)}, ND)
    assert ok, msg
    copie = app.AVANT_DATE_DIR / chemin.relative_to(app.LOCAL_ROOT)
    assert copie.read_bytes() == avant and chemin.read_bytes() != avant
    # Seconde écriture : la copie de la première version n'est pas remplacée
    ok, msg = app.write_date({"path": str(chemin)}, ND + dt.timedelta(days=1))
    assert ok, msg
    assert copie.read_bytes() == avant
    assert not list(copie.parent.glob(".*"))                   # aucun fichier provisoire oublié


@exiftool
def test_sans_copie_possible_la_date_n_est_pas_ecrite(app, monkeypatch):
    chemin = _photo(app)
    avant = chemin.read_bytes()
    app.AVANT_DATE_DIR.parent.mkdir(parents=True, exist_ok=True)
    app.AVANT_DATE_DIR.write_text("pas un dossier")          # la copie échoue
    ok, msg = app.write_date({"path": str(chemin)}, ND)
    assert ok is False and "copie de l'original impossible" in msg
    assert chemin.read_bytes() == avant


@exiftool
def test_une_video_n_est_pas_copiee(app):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    dossier = app.SCAN_DIR / "2008"
    dossier.mkdir(parents=True, exist_ok=True)
    video = dossier / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x32:d=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    ok, msg = app.write_date({"path": str(video)}, ND)
    assert ok, msg
    assert not app.AVANT_DATE_DIR.exists() or not any(app.AVANT_DATE_DIR.rglob("*.mp4"))


# ---------------------------------------------------------------- M48 : écrire sous le même verrou

@pytest.mark.parametrize("sauver", ["save_corrected", "save_validated", "save_results"])
def test_l_etat_est_ecrit_sous_le_verrou_de_sa_photographie(app, monkeypatch, sauver):
    vu = []
    ecrire = app.ecrire_json

    def espion(path, texte):
        vu.append(app.LOCK.locked())
        ecrire(path, texte)

    monkeypatch.setattr(app.module_de("ecrire_json"), "ecrire_json", espion)
    getattr(app, sauver)()
    assert vu == [True]
