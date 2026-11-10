"""Tests de la logique de dates et d'un passage complet analyse → correction (exiftool requis)."""
import datetime as dt
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ", "Europe/Paris")
    for k in ("IMMICH_URL", "IMMICH_API_KEY", "IMMICH_ROOT"):
        monkeypatch.delenv(k, raising=False)
    import app as module
    return importlib.reload(module)


@pytest.mark.parametrize("parts,attendu", [
    (("2019", "2019-07-14 Mariage"), ("jour", 2019, 7, 14)),
    (("1998", "1998-07 Numérisation Yves"), ("mois", 1998, 7, None)),
    (("2008", "2008 - BURGER !!!"), ("an", 2008, None, None)),
    (("Divers",), None),
])
def test_reference_dossier(app, parts, attendu):
    assert app.folder_ref(parts) == attendu


@pytest.mark.parametrize("nom,attendu,heure", [
    ("2008-08-10-014136052.jpg", dt.datetime(2008, 8, 10, 1, 41, 36), True),
    ("IMG_20260701_201320.jpg", dt.datetime(2026, 7, 1, 20, 13, 20), True),
    ("Screenshot_2019-07-14-15-20-00.png", dt.datetime(2019, 7, 14, 15, 20), True),
    ("IMG-20190714-WA0001.jpg", dt.datetime(2019, 7, 14, 12, 0), False),
    ("DSC01234.JPG", None, False),
    ("1499123456789.jpg", None, False),
])
def test_date_nom_fichier(app, nom, attendu, heure):
    assert app.name_date(nom) == (attendu, heure)


def test_proposition_garde_heure(app):
    ref = ("jour", 2019, 7, 14)
    assert app.propose(ref, dt.datetime(2021, 3, 5, 10, 11, 12)) == dt.datetime(2019, 7, 14, 10, 11, 12)
    assert app.propose(("mois", 1998, 7, None), None) == dt.datetime(1998, 7, 1, 12, 0)


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_analyse_et_correction(app):
    burger = app.SCAN_DIR / "2008" / "2008 - BURGER !!!"
    burger.mkdir(parents=True)
    img = Image.new("RGB", (30, 30), "blue")
    for n in ("2008-08-10-014136052.jpg", "IMG_20110502_101010.jpg", "20080305.jpg"):
        img.save(burger / n)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2022:12:24 14:42:39",
                    str(burger / "2008-08-10-014136052.jpg")], check=True)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2008:03:05 09:00:00",
                    str(burger / "20080305.jpg")], check=True)

    app.scan_worker()
    assert app.STATE["error"] is None
    items = {i["name"]: i for i in app.STATE["items"]}
    assert set(items) == {"2008-08-10-014136052.jpg", "IMG_20110502_101010.jpg"}
    assert items["2008-08-10-014136052.jpg"]["proposed"] == "2008-08-10T01:41:36"
    assert items["IMG_20110502_101010.jpg"]["status"] == "à trancher"
    assert items["IMG_20110502_101010.jpg"]["proposed"] == ""

    client = app.app.test_client()
    assert client.get("/").status_code == 200
    it = items["2008-08-10-014136052.jpg"]
    client.post("/apply", data={"sel": it["id"], f"date_{it['id']}": it["proposed"]})
    assert it["result"]["ok"], it["result"]
    assert not list(burger.glob("*_original"))
