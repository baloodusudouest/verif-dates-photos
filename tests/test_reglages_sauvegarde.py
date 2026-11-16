"""3.2.0 : réglages dans l'appli (reprise unique des anciennes variables), sauvegarde pour l'Anneau
(GET /externe/sauvegarde, jeton commun) et restauration. Sans réseau : faux Anneau par l'argument lire=."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outil import charger  # noqa: E402

MDP = "essai-ephemeride"
JETON = "jeton-commun-de-la-maison"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAISON_PASSWORD", MDP)
    for k in ("VERIF_PASSWORD", "VERIF_LECTURE_SEULE", "MAISON_LECTURE_SEULE", "MAISON_JETON", "MAISON_JETON_ANCIEN",
              "SCAN_SUBDIR", "TOLERANCE_DAYS", "IMMICH_URL", "IMMICH_API_KEY", "IMMICH_ROOT", "ORIGINALS_DIR"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def demarrer():
    module = charger()
    module.ETAT["attente"] = False
    return module


def connecte(module):
    c = TestClient(module.app)
    assert c.post("/api/connexion", json={"mot_de_passe": MDP}).status_code == 200
    return c


# ---------------------------------------------------------------- réglages d'usage

def test_defauts_sans_ancienne_variable(env):
    m = demarrer()
    with TestClient(m.app):                                                 # cycle de démarrage
        pass
    assert m.USAGE.tout() == {"sous_dossier": "Pellicule", "tolerance_jours": 1}
    assert m.SCAN_DIR == env / "photos" / "Pellicule" and m.TOLERANCE_DAYS == 1
    assert not m.LOG_FILE.exists()                                          # rien de repris : rien au journal


def test_reprise_au_premier_demarrage_puis_variable_ignoree(env, monkeypatch):
    monkeypatch.setenv("SCAN_SUBDIR", "Scans")
    monkeypatch.setenv("TOLERANCE_DAYS", "3")
    m = demarrer()
    with TestClient(m.app):                                                 # le démarrage écrit les reprises au journal
        pass
    assert m.USAGE.tout() == {"sous_dossier": "Scans", "tolerance_jours": 3}
    assert m.SCAN_DIR == env / "photos" / "Scans" and m.TOLERANCE_DAYS == 3
    journal = m.LOG_FILE.read_text(encoding="utf-8")
    assert "sous_dossier repris de la variable SCAN_SUBDIR" in journal
    assert "tolerance_jours repris de la variable TOLERANCE_DAYS : 3" in journal
    assert json.loads((env / "data" / "reglages.json").read_text()) == {"sous_dossier": "Scans", "tolerance_jours": 3}
    # second démarrage : la stack dit autre chose, elle n'est plus lue
    monkeypatch.setenv("SCAN_SUBDIR", "Autre")
    monkeypatch.setenv("TOLERANCE_DAYS", "9")
    m = demarrer()
    assert m.USAGE.tout() == {"sous_dossier": "Scans", "tolerance_jours": 3} and m.USAGE.repris == []
    assert m.TOLERANCE_DAYS == 3


def test_put_reglages_effet_sans_redemarrer(env):
    m = demarrer()
    c = connecte(m)
    v = c.get("/api/reglages").json()
    assert v["reglages"] == {"sous_dossier": "Pellicule", "tolerance_jours": 1}
    assert set(v["fixes"]) == {"lecture_seule", "lecture_seule_origine", "originaux_gardes", "dossier_present"}
    r = c.put("/api/reglages", json={"sous_dossier": "2019/Scans", "tolerance_jours": 4})
    assert r.status_code == 200, r.text
    assert r.json()["reglages"] == {"sous_dossier": "2019/Scans", "tolerance_jours": 4}
    assert m.SCAN_DIR == env / "photos" / "2019" / "Scans" and m.TOLERANCE_DAYS == 4   # tout de suite
    e = c.get("/api/etat").json()
    assert e["tolerance"] == 4 and e["dossier"] == "Scans"
    assert m.cache_params()["tol"] == 4                                      # le mode incrémental repart de zéro
    assert "tolerance_jours : 1 → 4" in m.LOG_FILE.read_text(encoding="utf-8")
    assert demarrer().USAGE.tout()["tolerance_jours"] == 4                   # gardé au redémarrage


@pytest.mark.parametrize("corps,mot", [
    ({"tolerance_jours": "beaucoup"}, "entier"),
    ({"tolerance_jours": 1.5}, "entier"),
    ({"tolerance_jours": True}, "entier"),
    ({"tolerance_jours": None}, "entier"),
    ({"tolerance_jours": -1}, "entre 0"),
    ({"tolerance_jours": 5000}, "entre 0"),
    ({"sous_dossier": 12}, "texte"),
    ({"sous_dossier": ["a"]}, "texte"),
    ({"sous_dossier": "../etc"}, ".."),
    ({"sous_dossier": "a/../../b"}, ".."),
    ({"sous_dossier": "~/x"}, ".."),
])
def test_put_reglages_mauvais_type_400(env, corps, mot):
    m = demarrer()
    c = connecte(m)
    r = c.put("/api/reglages", json=corps)
    assert r.status_code == 400 and mot in r.json()["detail"]
    assert m.USAGE.tout() == {"sous_dossier": "Pellicule", "tolerance_jours": 1}


def test_reglages_demandent_la_session_et_attendent_la_fin_d_une_tache(env):
    m = demarrer()
    anonyme = TestClient(m.app)
    assert anonyme.get("/api/reglages").status_code == 401
    assert anonyme.put("/api/reglages", json={"tolerance_jours": 2}).status_code == 401
    assert anonyme.get("/api/restaurer").status_code == 401
    assert anonyme.post("/api/restaurer", json={"le": "2026-11-15"}).status_code == 401
    c = connecte(m)
    m.STATE["running"] = True
    try:
        assert c.put("/api/reglages", json={"tolerance_jours": 2}).status_code == 409
    finally:
        m.STATE["running"] = False


def test_reglages_permis_en_lecture_seule(env, monkeypatch):
    monkeypatch.setenv("MAISON_LECTURE_SEULE", "oui")
    m = demarrer()
    c = connecte(m)
    assert c.put("/api/reglages", json={"tolerance_jours": 2}).status_code == 200
    assert c.get("/api/reglages").json()["fixes"]["lecture_seule"] is True


# ---------------------------------------------------------------- porte /externe/sauvegarde

def test_sauvegarde_porte_du_jeton_commun(env, monkeypatch):
    m = demarrer()
    c = TestClient(m.app)
    assert c.get("/externe/sauvegarde").status_code == 404                  # MAISON_JETON vide : porte fermée
    assert c.get("/externe/sauvegarde", headers={"Authorization": "Bearer "}).status_code == 404
    monkeypatch.setenv("MAISON_JETON", JETON)
    assert c.get("/externe/sauvegarde").status_code == 404                  # sans jeton
    assert c.get("/externe/sauvegarde", headers={"Authorization": "Bearer faux"}).status_code == 404
    # un ancien jeton (variable propre, d'avant le jeton commun) n'ouvre rien : seul MAISON_JETON compte
    monkeypatch.setenv("VERIF_JETON", "ancien-jeton-par-paire")
    assert c.get("/externe/sauvegarde", headers={"Authorization": "Bearer ancien-jeton-par-paire"}).status_code == 404
    # la session de l'appli n'ouvre pas non plus cette porte
    assert connecte(m).get("/externe/sauvegarde").status_code == 404
    r = c.get("/externe/sauvegarde", headers={"Authorization": f"Bearer {JETON}"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    s = r.json()
    assert s["contrat"] == 1 and s["appli"] == "verif-dates-photos" and s["version"] == m.__version__
    assert s["reglages"] == {"sous_dossier": "Pellicule", "tolerance_jours": 1}
    assert c.get("/externe/sauvegarde", headers={"X-Maison-Jeton": JETON}).status_code == 200


def test_sauvegarde_sans_secret_ni_chemin(env, monkeypatch):
    monkeypatch.setenv("MAISON_JETON", JETON)
    monkeypatch.setenv("IMMICH_URL", "http://immich.essai:2283")
    monkeypatch.setenv("IMMICH_API_KEY", "cle-immich-secrete")
    monkeypatch.setenv("IMMICH_ROOT", "/racine-immich")
    monkeypatch.setenv("ORIGINALS_DIR", "/originaux-perso")
    m = demarrer()
    texte = TestClient(m.app).get("/externe/sauvegarde", headers={"Authorization": f"Bearer {JETON}"}).text
    for interdit in (MDP, JETON, "cle-immich-secrete", "immich.essai", "/racine-immich", "/originaux-perso",
                     str(env), "lecture_seule", "originaux_gardes", "dossier_present"):
        assert interdit not in texte, interdit
    # la liste d'exclusion de l'appli est bien appliquée par le module
    assert m.maison.reglages_publics({"lecture_seule": True, "x": 1}, m.EXCLUS_SAUVEGARDE) == {"x": 1}


def test_portes_api_fermees_au_jeton_commun(env, monkeypatch):
    """Le jeton commun n'ouvre que /externe/sauvegarde : le reste de l'API demande la session."""
    monkeypatch.setenv("MAISON_JETON", JETON)
    m = demarrer()
    c = TestClient(m.app)
    for chemin in ("/api/etat", "/api/reglages", "/api/restaurer", "/api/journal.csv"):
        assert c.get(chemin, headers={"Authorization": f"Bearer {JETON}"}).status_code == 401, chemin
    assert set(c.get("/health").json()) == {"ok", "version", "a_traiter", "en_cours"}


# ---------------------------------------------------------------- restauration

def faux_anneau(sauvegardes, appels=None):
    """sauvegardes : {date: reglages}. Imite GET /externe/sauvegardes/<appli>[?le=…] de l'Anneau."""
    def lire(adresse, jeton):
        if appels is not None:
            appels.append((adresse, jeton))
        if "?le=" not in adresse:
            return {"dates": sorted(sauvegardes, reverse=True)}
        le = adresse.split("?le=")[1]
        if le not in sauvegardes:
            raise urllib.error.HTTPError(adresse, 404, "Not Found", {}, io.BytesIO(b""))
        return {"le": le, "version": "3.2.0", "reglages": sauvegardes[le]}
    return lire


def test_restaurer_dates_differences_application(env, monkeypatch):
    monkeypatch.setenv("MAISON_JETON", JETON)
    m = demarrer()
    appels = []
    m.ETAT["lire_sauvegarde"] = faux_anneau({
        "2026-11-14": {"sous_dossier": "Pellicule", "tolerance_jours": 1},
        "2026-11-15": {"sous_dossier": "Scans", "tolerance_jours": 2, "lecture_seule": True, "cle_inconnue": 5},
    }, appels)
    c = connecte(m)
    assert c.get("/api/restaurer").json() == {"dates": ["2026-11-15", "2026-11-14"]}
    assert appels[0] == ("http://anneau:8094/externe/sauvegardes/verif-dates-photos", JETON)   # jeton commun envoyé
    v = c.get("/api/restaurer", params={"le": "2026-11-15"}).json()
    assert v["le"] == "2026-11-15" and v["version"] == "3.2.0"
    assert v["differences"] == [{"cle": "sous_dossier", "actuel": "Pellicule", "sauve": "Scans"},
                                {"cle": "tolerance_jours", "actuel": 1, "sauve": 2}]   # ni clé exclue ni inconnue
    assert c.get("/api/restaurer", params={"le": "2026-11-14"}).json()["differences"] == []
    r = c.post("/api/restaurer", json={"le": "2026-11-15"})
    assert r.status_code == 200, r.text
    assert r.json()["reglages"] == {"sous_dossier": "Scans", "tolerance_jours": 2} and r.json()["restaures"] == 2
    assert m.TOLERANCE_DAYS == 2 and m.SCAN_DIR.name == "Scans"
    assert m.LECTURE_SEULE is False                                          # clé exclue : jamais restaurée
    assert "Restauré depuis la sauvegarde du 2026-11-15" in m.LOG_FILE.read_text(encoding="utf-8")
    assert c.get("/api/restaurer", params={"le": "2026-11-15"}).json()["differences"] == []


def test_restaurer_rien_ou_anneau_injoignable(env):
    m = demarrer()
    c = connecte(m)
    m.ETAT["lire_sauvegarde"] = faux_anneau({})
    r = c.get("/api/restaurer")
    assert r.status_code == 404 and "aucune sauvegarde" in r.json()["detail"]
    r = c.get("/api/restaurer", params={"le": "2026-11-15"})
    assert r.status_code == 404 and "2026-11-15" in r.json()["detail"]
    assert c.post("/api/restaurer", json={"le": "2026-11-15"}).status_code == 404

    def injoignable(adresse, jeton):
        raise urllib.error.URLError("refus de connexion")
    m.ETAT["lire_sauvegarde"] = injoignable
    for r in (c.get("/api/restaurer"), c.get("/api/restaurer", params={"le": "2026-11-15"}),
              c.post("/api/restaurer", json={"le": "2026-11-15"})):
        assert r.status_code == 503 and "injoignable" in r.json()["detail"]
    assert c.post("/api/restaurer", json={"le": "hier"}).status_code == 400
    assert m.USAGE.tout() == {"sous_dossier": "Pellicule", "tolerance_jours": 1}


def test_restaurer_permis_en_lecture_seule(env, monkeypatch):
    monkeypatch.setenv("VERIF_LECTURE_SEULE", "oui")
    m = demarrer()
    m.ETAT["lire_sauvegarde"] = faux_anneau({"2026-11-15": {"sous_dossier": "Pellicule", "tolerance_jours": 5}})
    r = connecte(m).post("/api/restaurer", json={"le": "2026-11-15"})
    assert r.status_code == 200 and r.json()["reglages"]["tolerance_jours"] == 5


def test_restaurer_valeur_sauvegardee_invalide(env):
    m = demarrer()
    m.ETAT["lire_sauvegarde"] = faux_anneau({"2026-11-15": {"sous_dossier": "../dehors", "tolerance_jours": 1}})
    r = connecte(m).post("/api/restaurer", json={"le": "2026-11-15"})
    assert r.status_code == 400 and m.USAGE.get("sous_dossier") == "Pellicule"
