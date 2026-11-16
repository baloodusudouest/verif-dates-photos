"""Tests de la logique de dates et d'un passage complet analyse → correction (exiftool requis)."""
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outil import charger  # noqa: E402


MDP = "essai"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ", "Europe/Paris")
    for k in ("IMMICH_URL", "IMMICH_API_KEY", "IMMICH_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VERIF_PASSWORD", MDP)
    monkeypatch.delenv("VERIF_LECTURE_SEULE", raising=False)
    for k in ("MAISON_PASSWORD", "MAISON_LECTURE_SEULE"):     # 3.1.0 : variables communes, absentes par défaut
        monkeypatch.delenv(k, raising=False)
    module = charger()
    module.ETAT["attente"] = False      # pas d'attente d'une seconde après chaque connexion

    # 3.0.0 : l'API FastAPI. Le client de test ouvre la session en naissant, comme avant.
    def client_connecte():
        c = TestClient(module.app)
        assert c.post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 200
        return c

    module.app.test_client = client_connecte
    return module


def etat(client):
    r = client.get("/api/etat")
    assert r.status_code == 200, r.text
    return r.json()


def photo(e, nom):
    return next(p for d in e["dossiers"] for p in d["photos"] if p["name"] == nom)


def corriger(client, *paires):
    """Comme le bouton « Corriger la sélection » : [(id, date)]."""
    return client.post("/api/corriger", json={"photos": [{"id": i, "date": d} for i, d in paires]})


@pytest.mark.parametrize("parts,attendu", [
    (("2019", "2019-07-14 Mariage"), ("jour", 2019, 7, 14)),
    (("1998", "1998-07 Numérisation"), ("mois", 1998, 7, None)),
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
    assert etat(client)["a_traiter"] == 2
    it = items["2008-08-10-014136052.jpg"]
    assert corriger(client, (it["id"], it["proposed"])).json() == {"ok": True, "n": 1}
    app.APPLY_THREAD.join(timeout=60)
    assert it["result"]["ok"], it["result"]
    assert "1 fichier(s) corrigé(s)" in app.STATE["messages"][0]
    assert client.get("/api/progression").json()["running"] is False
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
    assert etat(client)["has_cache"] is True          # « Scanner les nouveaux » proposé

    # nouveau fichier + correction de b.jpg (modifié) → seuls ces deux-là sont relus
    img.save(dossier / "c.jpg")
    b = next(i for i in app.STATE["items"] if i["name"] == "b.jpg")
    corriger(client, (b["id"], b["proposed"]))
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
    e = etat(client)
    assert photo(e, "faux.jpg")["voisins"]["min"] == "2008-08-10" and e["nb_corrigees"] == 0
    assert "path" not in photo(e, "faux.jpg")             # le chemin complet reste sur le serveur

    corriger(client, (faux["id"], faux["proposed"]))
    app.APPLY_THREAD.join(timeout=60)
    assert faux["result"]["ok"]
    e = etat(client)
    assert e["a_traiter"] == 2 and e["nb_corrigees"] == 1
    fait = e["corrigees"][0]["photos"][0]
    assert fait["avant"] == "24/12/2022 14:42:39" and fait["apres"].startswith("10/08/2008 14:42:39")
    assert client.get(f"/api/miniature/{faux['id']}").status_code == 200

    # après un nouveau scan, la photo corrigée n'est plus signalée mais reste dans « Déjà corrigées »
    app.scan_worker("nouveaux")
    assert "faux.jpg" not in {i["name"] for i in app.STATE["items"]}
    assert etat(client)["nb_corrigees"] == 1

    # si l'écart revient, elle repasse « À traiter » avec la mention
    _exif(burger / "faux.jpg", "2022:12:24 14:42:39")
    app.scan_worker("nouveaux")
    revenu = next(i for i in app.STATE["items"] if i["name"] == "faux.jpg")
    assert revenu["deja"]
    e = etat(client)
    assert e["nb_corrigees"] == 0 and photo(e, "faux.jpg")["deja"]      # « écart toujours présent »


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
    assert {i["status"] for i in items.values()} == {"sans date"}
    client = app.app.test_client()
    assert all(p["convertir"] for d in etat(client)["dossiers"] for p in d["photos"])

    r = corriger(client, *[(i["id"], "2015-06-20T15:30:00") for i in items.values()])
    assert r.json() == {"ok": True, "n": 3}, r.text     # 3.0.0 : le .mts (flux MPEG-TS) n'est plus pris pour un fichier abîmé
    app.APPLY_THREAD.join(timeout=300)
    for nom, it in items.items():
        assert it["result"]["ok"], (nom, it["result"])
    restants = sorted(p.name for p in dossier.iterdir())
    assert restants == ["00001.mp4", "clip.mp4", "clip2.mp4"]       # originaux supprimés, pas de fichier temporaire
    assert "copie sans perte" in items["clip2.mkv"]["result"]["msg"]

    app.scan_worker("nouveaux")
    assert app.STATE["items"] == []                                   # les MP4 ont la bonne date
    assert etat(client)["nb_corrigees"] == 3


@pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="exiftool/ffmpeg absents")
def test_conversion_garde_original(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.module_de("ORIGINALS_DIR"), "ORIGINALS_DIR", str(tmp_path / "originaux"))
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
            vus.append(client.get("/api/progression").json())
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


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_fichiers_mal_nommes_ou_abimes(app):
    dossier = app.SCAN_DIR / "2008" / "2008 - BURGER !!!"
    dossier.mkdir(parents=True)
    img = Image.new("RGB", (64, 64), "red")
    img.save(dossier / "png.jpg", "PNG")                         # PNG nommé .jpg
    img.save(dossier / "vrai.jpg")
    jpeg = (dossier / "vrai.jpg").read_bytes()
    (dossier / "vrai.jpg").unlink()
    (dossier / "entete.jpg").write_bytes(b"XXXX" + jpeg)          # octets parasites avant le JPEG
    (dossier / "tronque.jpg").write_bytes(jpeg[:120])            # JPEG coupé
    (dossier / "bruit.jpg").write_bytes(bytes(range(256)) * 20)  # illisible

    app.scan_worker("tout")
    items = {i["name"]: i for i in app.STATE["items"]}
    assert items["bruit.jpg"]["status"] == "abîmé" and app.STATE["stats"]["abime"] == 1
    assert items["png.jpg"]["vrai_ext"] == "png"
    client = app.app.test_client()
    e = etat(client)
    assert photo(e, "png.jpg")["vrai_ext"] == "png" and e["compte"]["abîmé"] == 1
    r = corriger(client, (items["bruit.jpg"]["id"], "2008-08-10T12:00:00"))
    assert r.status_code == 400                                   # un fichier abîmé n'est jamais corrigé

    nd = app.dt.datetime(2008, 8, 10, 12, 0)
    ok, msg, notes = app.write_date_robust(items["png.jpg"], nd)
    assert ok, msg
    assert (dossier / "png.png").exists() and not (dossier / "png.jpg").exists()

    it = items["entete.jpg"]
    ok, msg, notes = app.write_date_robust(it, nd)
    assert ok, msg
    assert "4 octet(s) parasite(s)" in notes[0]
    assert (dossier / "entete.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    assert (app.CLEANED_DIR / "Pellicule" / "2008" / "2008 - BURGER !!!" / "entete.jpg").read_bytes()[:4] == b"XXXX"

    it = items["tronque.jpg"]
    avant = (dossier / "tronque.jpg").read_bytes()
    ok, msg, notes = app.write_date_robust(it, nd)
    assert not ok and it["status"] == "abîmé" and "fichier abîmé" in msg
    assert (dossier / "tronque.jpg").read_bytes() == avant        # jamais modifié


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_renommage_nom_deja_pris(app):
    dossier = app.SCAN_DIR / "2011" / "2011-12-31 Réveillon"
    dossier.mkdir(parents=True)
    Image.new("RGB", (64, 64), "blue").save(dossier / "a.jpg")
    (dossier / "a.heic").write_bytes((dossier / "a.jpg").read_bytes())      # doublon identique mal nommé
    Image.new("RGB", (64, 64), "red").save(dossier / "b.jpg")
    Image.new("RGB", (64, 64), "green").save(dossier / "b.heic", "JPEG")     # contenu différent, même nom
    app.scan_worker("tout")
    items = {i["name"]: i for i in app.STATE["items"]}
    nd = app.dt.datetime(2011, 12, 31, 20, 0)

    ok, msg, notes = app.write_date_robust(items["a.heic"], nd)
    assert ok, msg
    assert "doublon identique de a.jpg" in notes[0]
    assert not (dossier / "a.heic").exists()
    assert (app.DOUBLONS_DIR / "Pellicule" / "2011" / "2011-12-31 Réveillon" / "a.heic").exists()
    assert items["a.heic"]["path"].endswith("a.jpg")

    ok, msg, notes = app.write_date_robust(items["b.heic"], nd)
    assert ok, msg
    assert (dossier / "b-heic.jpg").exists() and (dossier / "b.jpg").exists()
    assert notes[0] == "renommé en b-heic.jpg"


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_dossier_mal_date_valider_ou_renommer(app):
    bisca = app.SCAN_DIR / "2007" / "2007 - Bisca et dune du pila"
    bisca.mkdir(parents=True)
    img = Image.new("RGB", (8, 8), "blue")
    for n in ("2008-02-03-115924195.jpg", "2008-02-03-115946196.jpg", "2008-02-03-120101000.jpg"):
        img.save(bisca / n)
    _exif(bisca / "2008-02-03-115924195.jpg", "2008:02:03 11:59:24")
    autre = app.SCAN_DIR / "2009" / "2009 - Pâques"
    autre.mkdir(parents=True)
    for n in ("2010-04-04-100000.jpg", "2010-04-05-100000.jpg"):
        img.save(autre / n)
    client = app.app.test_client()

    app.scan_worker("tout")
    folders = app.STATE["folders"]
    assert folders["2007/2007 - Bisca et dune du pila"]["nouveau"] == "2008/2008-02-03 Bisca et dune du pila"
    assert folders["2009/2009 - Pâques"]["nouveau"] == "2010/2010-04 Pâques"
    prop = next(d for d in etat(client)["dossiers"] if d["nom"] == "2007/2007 - Bisca et dune du pila")["proposition"]
    assert prop["nouveau"] == "2008/2008-02-03 Bisca et dune du pila" and "3/3 photos datées du 03/02/2008" in prop["label"]

    # validation telle quelle : plus signalées, rien de modifié
    avant = {p.name: p.stat().st_mtime_ns for p in bisca.iterdir()}
    r = client.post("/api/valider", json={"folder": "2007/2007 - Bisca et dune du pila"})
    assert r.json()["n"] == 3
    assert etat(client)["nb_corrigees"] == 3
    app.scan_worker("nouveaux")
    assert {i["folder"] for i in app.STATE["items"]} == {"2009/2009 - Pâques"}
    assert app.STATE["stats"]["valide"] == 3
    assert {p.name: p.stat().st_mtime_ns for p in bisca.iterdir()} == avant

    # renommage proposé
    r = client.post("/api/renommer", json={"folder": "2009/2009 - Pâques", "nouveau": "2010/2010-04 Pâques"})
    assert r.json()["ok"]
    for _ in range(100):
        if not app.STATE["running"]:
            break
        __import__("time").sleep(0.1)
    assert (app.SCAN_DIR / "2010" / "2010-04 Pâques" / "2010-04-04-100000.jpg").exists()
    assert not autre.exists()
    # dossier bien daté : plus de conflit nom/dossier, reste à écrire la date (photos sans EXIF)
    assert {i["status"] for i in app.STATE["items"]} == {"sans date"}
    assert sorted(i["proposed"] for i in app.STATE["items"]) == ["2010-04-04T10:00:00", "2010-04-05T10:00:00"]
    assert "Dossier renommé" in app.STATE["messages"][0]

    # garde-fous
    assert client.post("/api/renommer", json={"folder": "2007/2007 - Bisca et dune du pila", "nouveau": "../evasion"}).status_code == 400
    assert client.post("/api/renommer", json={"folder": "2007/2007 - Bisca et dune du pila", "nouveau": "2010/2010-04 Pâques"}).status_code == 409


def _mov_moov_en_fin(path, creation="2014-08-31T02:18:25+0200"):
    """Vidéo façon iPhone : atome moov après les données, CreationDate dans les clés Apple."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=1",
                    "-c:v", "libx264", "-movflags", "use_metadata_tags",
                    "-metadata", f"com.apple.quicktime.creationdate={creation}", str(path)], check=True)


@pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="exiftool/ffmpeg absents")
def test_video_moov_en_fin_et_realignement(app):
    dossier = app.SCAN_DIR / "2014" / "2014-08 - Soirée jeux"
    dossier.mkdir(parents=True)
    bonne = dossier / "2014-08-31-1707031775.mov"
    _mov_moov_en_fin(bonne)
    data = bonne.read_bytes()
    assert data.find(b"mdat") < data.find(b"moov")          # moov bien après les données
    app.scan_worker("tout")
    assert app.STATE["items"] == []                          # date d'origine vue : plus de faux « sans date »

    # vidéo touchée à tort par une ancienne version : CreateDate écrasée, CreationDate intacte
    abimee = dossier / "2014-08-31-1710031774.mov"
    _mov_moov_en_fin(abimee, "2014-08-31T01:14:34+0200")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-api", "QuickTimeUTC",
                    "-QuickTime:CreateDate=2014:08:31 17:10:03", str(abimee)], check=True)
    with open(app.LOG_FILE, "w", encoding="utf-8", newline="") as f:
        f.write("horodatage;fichier;ancienne_date;source;nouvelle_date;statut;detail\n")
        f.write(f"2026-11-11T12:00:00;{abimee};;;31/08/2014 17:10:03;échec;relu 31/08/2014 01:14:34 (CreationDate)\n")
    client = app.app.test_client()
    assert etat(client)["a_realigner"] == 1           # « 1 vidéo(s) modifiée(s) à tort »
    app.STATE["running"] = False
    app.realign_worker(app.videos_a_realigner())
    rec = app.run_exiftool(abimee, False)[0]
    assert rec["CreateDate"] == rec["CreationDate"] == "2014-08-31 01:14:34"
    assert app.videos_a_realigner() == []
    assert "1 vidéo(s) réalignée(s)" in app.STATE["messages"][0]


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_xmp_nomme_jpg_signale_abime(app):
    dossier = app.SCAN_DIR / "2012" / "2012-05-01 Mai"
    dossier.mkdir(parents=True)
    (dossier / "IMG_0001.JPG").write_text('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta><?xpacket end="w"?>')
    app.scan_worker("tout")
    it = app.STATE["items"][0]
    assert it["status"] == "abîmé" and "XMP" in it["erreur"]


@pytest.mark.skipif(not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="exiftool/ffmpeg absents")
def test_realignement_video_autre_fuseau(app):
    """Vidéo tournée à New York (UTC-4) : CreationDate et CreateDate réalignées désignent le même instant."""
    dossier = app.SCAN_DIR / "2015" / "2015-10 - NEW YORK CITY!!!"
    dossier.mkdir(parents=True)
    v = dossier / "2015-10-14-0232200096.MOV"
    _mov_moov_en_fin(v, "2015-10-14T02:32:20-0400")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-api", "QuickTimeUTC",
                    "-QuickTime:CreateDate=2015:10:14 02:32:20", str(v)], check=True)
    with open(app.LOG_FILE, "w", encoding="utf-8", newline="") as f:
        f.write("horodatage;fichier;ancienne_date;source;nouvelle_date;statut;detail\n")
        f.write(f"2026-11-11T12:00:00;{v};;;14/10/2015 02:32:20;échec;relu 14/10/2015 02:32:20 (CreationDate)\n")
    app.realign_worker(app.videos_a_realigner())
    assert app.videos_a_realigner() == []
    assert "1 vidéo(s) réalignée(s)" in app.STATE["messages"][0]


def _empreintes(racine):
    import hashlib
    return {str(f.relative_to(racine)): (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns)
            for f in sorted(racine.rglob("*")) if f.is_file()}


def test_111_rien_sans_mot_de_passe(app, monkeypatch):
    anonyme = TestClient(app.app)
    assert anonyme.get("/health").json()["ok"] is True
    assert anonyme.get("/icone.svg").status_code == 200                     # icône sans session (Anneau, écran d'accueil)
    assert anonyme.get("/api/session").json()["connecte"] is False
    for chemin in ("/api/etat", "/api/progression", "/api/journal.csv", "/api/miniature/x"):
        assert anonyme.get(chemin).status_code == 401
    for chemin in ("/api/analyse", "/api/corriger", "/api/valider", "/api/renommer", "/api/realigner"):
        assert anonyme.post(chemin, json={}).status_code == 401
    assert anonyme.post("/api/connexion", json={"mot_de_passe": "faux"}).status_code == 401
    assert anonyme.get("/api/etat").status_code == 401                      # toujours dehors
    assert anonyme.post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 200
    assert anonyme.get("/api/etat").status_code == 200
    assert anonyme.post("/api/deconnexion").status_code == 200 and anonyme.get("/api/progression").status_code == 401
    # sans mot de passe configuré, l'outil ne s'ouvre pas du tout
    monkeypatch.setattr(app.module_de("PASSWORD"), "PASSWORD", "")
    assert anonyme.get("/api/etat").status_code == 503
    assert anonyme.post("/api/connexion", json={"mot_de_passe": ""}).status_code == 503
    # /health ne donne que des nombres : aucun nom de fichier ni de dossier
    assert set(anonyme.get("/health").json()) == {"ok", "version", "a_traiter", "en_cours"}


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_111_lecture_seule_aucun_fichier_ne_bouge(app, monkeypatch):
    """En lecture seule : l'analyse et les propositions fonctionnent, mais aucun octet ni aucune date
    de fichier ne change, quel que soit le geste demandé."""
    dossier = app.SCAN_DIR / "2019" / "2019-07-14 Mariage"
    dossier.mkdir(parents=True)
    photo = dossier / "a.jpg"
    Image.new("RGB", (30, 30), "blue").save(photo)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2010:01:01 10:00:00", str(photo)], check=True)
    app.scan_worker()
    it = next(i for i in app.STATE["items"] if i["name"] == "a.jpg")
    assert it["status"] == "écart" and it["proposed"]
    monkeypatch.setattr(app.module_de("LECTURE_SEULE"), "LECTURE_SEULE", True)
    avant = _empreintes(app.SCAN_DIR)
    client = app.app.test_client()
    assert etat(client)["lecture_seule"] is True
    assert client.get("/api/session").json()["lecture_seule"] is True
    r = corriger(client, (it["id"], it["proposed"]))
    assert r.status_code == 409 and "lecture seule" in r.json()["detail"]
    assert client.get("/api/progression").json()["running"] is False
    assert client.post("/api/renommer", json={"folder": "2019/2019-07-14 Mariage", "nouveau": "2010/2010-01-01 Autre"}).status_code == 409
    assert client.post("/api/realigner").status_code == 409
    for ecrire in (lambda: app.write_date(it, app.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: app.write_date_robust(it, app.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: app.convert_and_date(it, app.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: app.rename_to_real_ext(it, "png"), lambda: app.strip_jpeg_header(it),
                   lambda: app.realign_worker([str(photo)])):
        with pytest.raises(app.LectureSeule):
            ecrire()
    assert _empreintes(app.SCAN_DIR) == avant and dossier.is_dir()


def test_ecritures_simultanees(app):
    """Plusieurs écritures en même temps (corrections, validation, journal) ne doivent ni planter
    ni laisser un fichier d'état illisible ou une ligne de journal perdue."""
    import json as _json
    import threading as _th
    erreurs = []

    def modifie(base):
        try:
            for i in range(300):
                with app.LOCK:
                    app.CORRECTED[f"/x/{base}-{i}.jpg"] = {"id": f"{base}{i}", "path": f"/x/{base}-{i}.jpg"}
                app.save_corrected()
        except Exception as exc:   # « dictionary changed size during iteration », fichier .tmp disputé…
            erreurs.append(repr(exc))

    def note(base):
        try:
            for i in range(200):
                app.noter_journal(["t", f"/x/{base}-{i}.jpg", "", "", "", "ok", "essai"])
        except Exception as exc:
            erreurs.append(repr(exc))

    fils = [_th.Thread(target=modifie, args=(b,)) for b in "abcd"] + [_th.Thread(target=note, args=(b,)) for b in "efgh"]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert erreurs == []
    assert len(_json.loads(app.CORRECTED_FILE.read_text(encoding="utf-8"))) == 4 * 300
    lignes = app.LOG_FILE.read_text(encoding="utf-8").splitlines()
    assert lignes[0].startswith("horodatage;") and len(lignes) == 1 + 4 * 200
    assert not list(app.DATA_DIR.glob(".*.tmp"))          # aucun fichier temporaire oublié


# ================================================================ 3.1.0 : variables communes de la maison

def test_310_module_maison_copie_non_retouchee():
    from app import maison
    assert maison.empreinte_calculee() == maison.empreinte_ecrite()


def test_310_mot_de_passe_commun_puis_repli(app, monkeypatch):
    """MAISON_PASSWORD l'emporte ; sans lui, VERIF_PASSWORD (la stack actuelle) ; sans aucun, rien ne s'ouvre."""
    assert app.PASSWORD == MDP                                   # stack actuelle : VERIF_PASSWORD seul
    monkeypatch.setenv("MAISON_PASSWORD", "mot-de-passe-commun")
    module = charger()
    module.ETAT["attente"] = False
    assert module.PASSWORD == "mot-de-passe-commun"
    c = TestClient(module.app)
    assert c.post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 401
    assert c.post("/api/connexion", json={"mot_de_passe": "mot-de-passe-commun"}).status_code == 200
    assert c.get("/api/etat").status_code == 200
    monkeypatch.delenv("MAISON_PASSWORD")
    monkeypatch.delenv("VERIF_PASSWORD")
    module = charger()
    r = TestClient(module.app).get("/api/etat")
    assert r.status_code == 503 and "MAISON_PASSWORD (ou VERIF_PASSWORD)" in r.json()["detail"]


@pytest.mark.parametrize("maison_ls,propre,attendu,origine", [
    (None, None, False, ""),
    (None, "oui", True, "appli"),
    (None, "1", True, "appli"),              # valeurs acceptées avant la 3.1.0
    (None, "true", True, "appli"),
    (None, "yes", True, "appli"),
    ("oui", None, True, "maison"),
    ("oui", "", True, "maison"),             # variable de la stack posée vide : la maison décide
    ("oui", "non", False, ""),               # l'appli forcée en écriture malgré la maison
    ("non", "oui", True, "appli"),
    ("", "", False, ""),
])
def test_310_lecture_seule_maison_et_forcage(app, monkeypatch, maison_ls, propre, attendu, origine):
    for nom, valeur in (("MAISON_LECTURE_SEULE", maison_ls), ("VERIF_LECTURE_SEULE", propre)):
        if valeur is None:
            monkeypatch.delenv(nom, raising=False)
        else:
            monkeypatch.setenv(nom, valeur)
    module = charger()
    assert module.LECTURE_SEULE is attendu and module.ORIGINE_LECTURE_SEULE == origine
    c = TestClient(module.app)
    c.post("/api/connexion", json={"mot_de_passe": MDP})
    s = c.get("/api/session").json()
    assert s["lecture_seule"] is attendu and s["lecture_seule_origine"] == origine
    e = etat(c)
    assert e["lecture_seule"] is attendu and e["lecture_seule_origine"] == origine


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
def test_310_lecture_seule_de_la_maison_aucun_fichier_ne_bouge(app, monkeypatch):
    """Preuve sans retouche du module : MAISON_LECTURE_SEULE=oui dans l'environnement suffit à tout refuser,
    à l'adresse comme dans chaque fonction qui touche à un fichier."""
    monkeypatch.setenv("MAISON_LECTURE_SEULE", "oui")
    module = charger()
    module.ETAT["attente"] = False
    dossier = module.SCAN_DIR / "2019" / "2019-07-14 Mariage"
    dossier.mkdir(parents=True)
    photo = dossier / "a.jpg"
    Image.new("RGB", (30, 30), "blue").save(photo)
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2010:01:01 10:00:00", str(photo)], check=True)
    module.scan_worker()
    it = next(i for i in module.STATE["items"] if i["name"] == "a.jpg")
    assert it["status"] == "écart" and it["proposed"]
    avant = _empreintes(module.SCAN_DIR)
    c = TestClient(module.app)
    assert c.post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 200
    e = etat(c)
    assert e["lecture_seule"] is True and e["lecture_seule_origine"] == "maison"
    r = corriger(c, (it["id"], it["proposed"]))
    assert r.status_code == 409 and "lecture seule" in r.json()["detail"]
    assert c.post("/api/renommer", json={"folder": "2019/2019-07-14 Mariage", "nouveau": "2010/2010-01-01 Autre"}).status_code == 409
    assert c.post("/api/realigner").status_code == 409
    for ecrire in (lambda: module.write_date(it, module.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: module.write_date_robust(it, module.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: module.convert_and_date(it, module.dt.datetime(2019, 7, 14, 10, 0)),
                   lambda: module.rename_to_real_ext(it, "png"), lambda: module.strip_jpeg_header(it),
                   lambda: module.realign_worker([str(photo)])):
        with pytest.raises(module.LectureSeule):
            ecrire()
    assert _empreintes(module.SCAN_DIR) == avant and dossier.is_dir()
