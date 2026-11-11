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
    app.APPLY_THREAD.join(timeout=60)
    assert it["result"]["ok"], it["result"]
    assert "1 fichier(s) corrigé(s)" in app.STATE["messages"][0]
    assert client.get("/progress").get_json()["running"] is False
    assert not list(burger.glob("*_original"))


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_progression(app):
    dossier = app.SCAN_DIR / "2019" / "2019-07-14 Mariage"
    dossier.mkdir(parents=True)
    (app.SCAN_DIR / "2019" / "@eaDir").mkdir()
    img = Image.new("RGB", (8, 8), "red")
    for n in range(250):
        img.save(dossier / f"p{n:03d}.jpg")
    img.save(app.SCAN_DIR / "2019" / "@eaDir" / "vignette.jpg")
    vus = []
    origine = app.set_progress

    def espion(**kw):
        origine(**kw)
        if "done" in kw and app.STATE["total"]:
            vus.append((app.STATE["done"], app.STATE["total"]))

    app.set_progress = espion
    app.scan_worker()
    assert app.STATE["stats"]["fichiers"] == 250          # @eaDir ignoré
    assert vus[-1] == (250, 250)
    assert len([v for v in vus if v[0] > 0]) == 3          # 3 lots de 100 max : la barre avance


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_scan_nouveaux_et_tout(app):
    dossier = app.SCAN_DIR / "2019" / "2019-07-14 Mariage"
    dossier.mkdir(parents=True)
    img = Image.new("RGB", (8, 8), "red")
    for n in ("a.jpg", "b.jpg"):
        img.save(dossier / n)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2019:07:14 10:00:00",
                    str(dossier / "a.jpg")], check=True)
    client = app.app.test_client()

    app.scan_worker("tout")
    assert app.CACHE_FILE.exists()
    assert app.STATE["stats"]["ok"] == 1 and app.STATE["stats"]["sans_date"] == 1
    assert "Scanner les nouveaux" in client.get("/").get_data(as_text=True)

    # nouveau fichier + correction de b.jpg (modifié) → seuls ces deux-là sont relus
    img.save(dossier / "c.jpg")
    b = next(i for i in app.STATE["items"] if i["name"] == "b.jpg")
    client.post("/apply", data={"sel": b["id"], f"date_{b['id']}": b["proposed"]})
    app.APPLY_THREAD.join(timeout=60)
    lus = []
    origine = app.run_exiftool_files
    app.run_exiftool_files = lambda paths: (lus.extend(p.name for p in paths), origine(paths))[1]
    app.scan_worker("nouveaux")
    assert sorted(lus) == ["b.jpg", "c.jpg"]
    assert app.STATE["stats"]["fichiers"] == 3 and app.STATE["stats"]["ok"] == 2
    assert [i["name"] for i in app.STATE["items"]] == ["c.jpg"]
    assert "2 fichier(s) relu(s), 1 repris" in app.STATE["messages"][0]

    lus.clear()
    app.scan_worker("tout")
    assert sorted(lus) == ["a.jpg", "b.jpg", "c.jpg"]
    app.run_exiftool_files = origine


def _exif(path, valeur):
    subprocess.run(["exiftool", "-q", "-overwrite_original", f"-DateTimeOriginal={valeur}", str(path)], check=True)


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_voisines_et_deja_corrigees(app):
    burger = app.SCAN_DIR / "2008" / "2008 - BURGER !!!"
    burger.mkdir(parents=True)
    img = Image.new("RGB", (8, 8), "green")
    for n in ("v1.jpg", "v2.jpg", "v3.jpg", "v4.jpg", "faux.jpg", "vide.jpg", "2008-08-11-120000.jpg"):
        img.save(burger / n)
    for n in ("v1.jpg", "v2.jpg", "v3.jpg"):
        _exif(burger / n, "2008:08:10 15:00:00")
    _exif(burger / "v4.jpg", "2008:08:12 18:00:00")
    _exif(burger / "faux.jpg", "2022:12:24 14:42:39")      # mauvaise date d'appareil
    _exif(burger / "2008-08-11-120000.jpg", "2022:12:24 14:42:39")

    app.scan_worker("tout")
    items = {i["name"]: i for i in app.STATE["items"]}
    assert set(items) == {"faux.jpg", "vide.jpg", "2008-08-11-120000.jpg"}

    faux = items["faux.jpg"]                           # dossier « 2008 » seul → jour dominant des voisines
    assert faux["proposed"] == "2008-08-10T14:42:39" and faux["proposed_src"] == "autres photos du dossier"
    assert faux["voisins"]["min"] == "2008-08-10" and faux["voisins"]["max"] == "2008-08-12"
    assert "3/4 photos" in faux["voisins"]["label"]
    assert items["vide.jpg"]["proposed"] == "2008-08-10T12:00:00"
    nom = items["2008-08-11-120000.jpg"]               # le nom du fichier reste prioritaire
    assert nom["proposed"] == "2008-08-11T12:00:00" and nom["proposed_src"] == "nom du fichier"
    assert nom["choice_voisins"] == "2008-08-10T14:42:39"

    client = app.app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'data-vmin="2008-08-10"' in html and "Déjà corrigées (0)" in html

    client.post("/apply", data={"sel": faux["id"], f"date_{faux['id']}": faux["proposed"]})
    app.APPLY_THREAD.join(timeout=60)
    assert faux["result"]["ok"]
    html = client.get("/").get_data(as_text=True)
    assert "À traiter — tout (2)" in html and "Déjà corrigées (1)" in html
    assert "Avant : 24/12/2022 14:42:39" in html and "10/08/2008 14:42:39" in html
    assert client.get(f"/thumb/{faux['id']}").status_code == 200

    # après un nouveau scan, la photo corrigée n'est plus signalée mais reste dans « Déjà corrigées »
    app.scan_worker("nouveaux")
    assert "faux.jpg" not in {i["name"] for i in app.STATE["items"]}
    assert "Déjà corrigées (1)" in client.get("/").get_data(as_text=True)

    # si l'écart revient, elle repasse « À traiter » avec la mention
    _exif(burger / "faux.jpg", "2022:12:24 14:42:39")
    app.scan_worker("nouveaux")
    revenu = next(i for i in app.STATE["items"] if i["name"] == "faux.jpg")
    assert revenu["deja"]
    html = client.get("/").get_data(as_text=True)
    assert "Déjà corrigées (0)" in html and "écart toujours présent" in html


def _video(path, *opts):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", *opts, str(path)], check=True)


@pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="exiftool/ffmpeg absents")
def test_conversion_mp4(app):
    dossier = app.SCAN_DIR / "2015" / "2015-06-20 Fête"
    dossier.mkdir(parents=True)
    _video(dossier / "clip.avi", "-c:v", "mjpeg", "-c:a", "pcm_s16le")
    _video(dossier / "clip2.mkv", "-c:v", "libx264", "-c:a", "aac")
    _video(dossier / "00001.mts", "-c:v", "libx264", "-c:a", "ac3", "-f", "mpegts")
    app.scan_worker("tout")
    items = {i["name"]: i for i in app.STATE["items"]}
    assert set(items) == {"clip.avi", "clip2.mkv", "00001.mts"}
    assert all(i["convertir"] for i in items.values())
    client = app.app.test_client()
    assert 'data-convertir="1"' in client.get("/").get_data(as_text=True)

    data = {"sel": [i["id"] for i in items.values()]}
    for i in items.values():
        data[f"date_{i['id']}"] = "2015-06-20T15:30:00"
    client.post("/apply", data=data)
    app.APPLY_THREAD.join(timeout=300)
    for nom, it in items.items():
        assert it["result"]["ok"], (nom, it["result"])
    restants = sorted(p.name for p in dossier.iterdir())
    assert restants == ["00001.mp4", "clip.mp4", "clip2.mp4"]       # originaux supprimés, pas de fichier temporaire
    assert "copie sans perte" in items["clip2.mkv"]["result"]["msg"]

    app.scan_worker("nouveaux")
    assert app.STATE["items"] == []                                   # les MP4 ont la bonne date
    assert "Déjà corrigées (3)" in client.get("/").get_data(as_text=True)


@pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="exiftool/ffmpeg absents")
def test_conversion_garde_original(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ORIGINALS_DIR", str(tmp_path / "originaux"))
    dossier = app.SCAN_DIR / "2015" / "2015-06-20 Fête"
    dossier.mkdir(parents=True)
    _video(dossier / "clip.avi", "-c:v", "mjpeg", "-c:a", "pcm_s16le")
    app.scan_worker("tout")
    it = app.STATE["items"][0]
    ok, msg, new = app.convert_and_date(it, app.dt.datetime(2015, 6, 20, 15, 30))
    assert ok, msg
    assert (tmp_path / "originaux" / "Pellicule" / "2015" / "2015-06-20 Fête" / "clip.avi").exists()
    assert new.name == "clip.mp4" and new.exists()


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_estimations_par_etape(app):
    dossier = app.SCAN_DIR / "2019" / "2019-07-14 Mariage"
    dossier.mkdir(parents=True)
    img = Image.new("RGB", (8, 8), "red")
    for n in range(120):
        img.save(dossier / f"p{n:03d}.jpg")
    client = app.app.test_client()
    app.STATE["running"] = True
    app.scan_worker("tout")                                    # 1re analyse : pas de mesures
    meta = app.load_meta()
    assert meta["files_total"] == 120 and meta["walk_rate"] > 0 and meta["analyse_rate"] > 0

    vus = []
    origine = app.run_exiftool_files

    def espion(paths):
        if not vus:
            vus.append(client.get("/progress").get_json())
        return origine(paths)

    app.run_exiftool_files = espion
    app.STATE["running"] = True
    app.scan_worker("tout")                                    # 2e analyse : étapes estimées
    app.run_exiftool_files = origine
    st = vus[0]
    etapes = {e["key"]: e for e in st["steps"]}
    assert etapes["index"]["status"] == "skipped"              # Immich non configuré dans les tests
    assert etapes["walk"]["status"] == "done" and etapes["walk"]["duration"] is not None
    assert etapes["analyse"]["status"] == "current" and etapes["analyse"]["estimate"] is not None
    assert st["total_remaining"] is not None
    html = client.get("/").get_data(as_text=True)
    assert 'id="prog-steps"' in html


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_javascript_valide(app, tmp_path):
    """Le script de la page doit se lire sans erreur de syntaxe (cas réel : apostrophe mal échappée)."""
    app.STATE.update(running=True, steps=[])
    html = app.app.test_client().get("/").get_data(as_text=True)
    app.STATE["running"] = False
    js = html.split("<script>")[1].split("</script>")[0]
    f = tmp_path / "page.js"
    f.write_text(js, encoding="utf-8")
    p = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
