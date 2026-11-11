"""Tests d'écran (2.0.0) : la page rhabillée garde ses gestes. Sur PC et sur téléphone, avec une
petite Pellicule de vrais fichiers. Le parcours qui compte : corriger un dossier, puis vérifier
que la date est bien dans le fichier."""
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool absent")
RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["pc", "telephone"])
def page(request, tmp_path):
    """Un outil neuf par test : ces tests écrivent dans des fichiers."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "tests/interface/serveur.py", str(tmp_path), str(port)], cwd=RACINE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        if (tmp_path / "pret").exists():
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("L'outil de démonstration n'a pas démarré.")
    time.sleep(0.6)
    mobile = request.param == "telephone"
    with playwright.sync_playwright() as p:
        nav = p.chromium.launch()
        ctx = nav.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 860},
                              is_mobile=mobile, has_touch=mobile)
        pg = ctx.new_page()
        erreurs = []
        pg.on("pageerror", lambda e: erreurs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(f"http://127.0.0.1:{port}/")
        pg.get_by_label(re.compile("Mot de passe")).fill("essai")
        pg.get_by_role("button", name="Se connecter").click()
        pg.wait_for_selector("nav.rail")
        pg.dossier = tmp_path
        yield pg
        assert not erreurs, erreurs
        nav.close()
    proc.terminate()


def test_20_rail_tuiles_et_sections(page):
    rail = page.locator("nav.rail a")
    assert [a.get_attribute("aria-label") for a in rail.all()] == ["À traiter", "Corrigées", "Connexion", "Réglages"]
    tuiles = page.locator("#tuiles .tuile")
    assert tuiles.count() == 7 and tuiles.first.get_attribute("aria-pressed") == "true"
    cartes = page.locator("#f .card")
    total = cartes.count()
    assert total == 4 and int(tuiles.first.locator(".chiffre").inner_text()) == total
    page.locator("#tuiles .tuile", has_text="Sans date").click()                      # une tuile filtre, comme l'ancien menu
    assert page.locator("#f .card:visible").count() == 1
    page.locator("#tuiles .tuile", has_text="Tout").click()
    assert page.locator("#f .card:visible").count() == total
    page.locator("nav.rail a[aria-label='Connexion']").click()
    page.get_by_role("heading", name="La Pellicule").wait_for()
    assert not page.locator("#f").is_visible()
    page.locator("nav.rail a[aria-label='Réglages']").click()
    page.get_by_role("link", name="Télécharger corrections.csv").wait_for()
    page.locator("nav.rail a[aria-label='Corrigées']").click()
    page.get_by_role("heading", name="Corrigées").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_20_corriger_un_dossier_ecrit_la_date_dans_le_fichier(page):
    """Le chemin entre le clic et le fichier : appliquer la date du nom au dossier, corriger, relire."""
    fichier = page.dossier / "photos" / "Pellicule" / "2019" / "2019-07-14 Mariage" / "IMG_20190714_161500.jpg"
    lire = lambda: subprocess.run(["exiftool", "-s3", "-DateTimeOriginal", str(fichier)], capture_output=True, text=True).stdout.strip()
    assert lire() == "2021:03:05 10:11:12"
    dossier = page.locator("[data-folder][data-nom='2019/2019-07-14 Mariage']")
    dossier.get_by_role("button", name=re.compile(r"^Date du nom")).click()
    assert "1 photo(s) réglée(s) et cochée(s)" in dossier.inner_text()               # le dossier se replie, comme avant
    page.get_by_role("button", name="Corriger la sélection").click()                 # la confirmation est acceptée
    page.wait_for_function("document.querySelector('nav.rail') && !document.querySelector('#prog:not([hidden])')", timeout=30000)
    for _ in range(60):
        if lire() == "2019:07:14 16:15:00":
            break
        time.sleep(0.25)
    assert lire() == "2019:07:14 16:15:00"
    page.goto(page.url.split("#")[0] + "#corrigees")
    page.reload()
    page.locator("#corrigees .card", has_text="IMG_20190714_161500.jpg").wait_for()
