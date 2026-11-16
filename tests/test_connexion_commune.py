"""2.1.0 : le cookie commun posé par l'Anneau ouvre l'appli si le mot de passe est le même."""
import hashlib
import hmac
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outil import charger  # noqa: E402
MDP = "mot-de-passe-essai"


def cookie(mot_de_passe, expire):
    cle = hashlib.sha256(b"maison-session|" + mot_de_passe.encode()).digest()
    return f"{expire}.{hmac.new(cle, str(expire).encode(), hashlib.sha256).hexdigest()}"


def test_connexion_commune(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAISON_PASSWORD", MDP)
    for k in ("VERIF_PASSWORD", "MAISON_LECTURE_SEULE", "MAISON_JETON"):     # seules les variables du test
        monkeypatch.delenv(k, raising=False)
    module = charger()
    dans_une_heure = int(time.time()) + 3600

    def etat(valeur):
        c = TestClient(module.app)
        if valeur is not None:
            c.cookies.set("maison_session", valeur)
        return c.get("/api/etat").status_code

    assert etat(None) == 401
    assert etat(cookie(MDP, dans_une_heure)) == 200
    assert etat(cookie("un-autre-mot-de-passe", dans_une_heure)) == 401
    assert etat(cookie(MDP, int(time.time()) - 5)) == 401
    for faux in ("", ".", "abc", "123.", "1.2.3"):
        assert etat(faux) == 401
    c = TestClient(module.app)
    c.cookies.set("maison_session", cookie(MDP, dans_une_heure))
    assert c.get("/api/session").json()["connecte"] is True          # déjà connecté : pas d'écran de mot de passe
    r = c.post("/api/deconnexion")
    assert 'maison_session=""' in r.headers.get("set-cookie", "")    # « Se déconnecter » retire le cookie commun
    c.cookies.clear()
    assert c.get("/api/etat").status_code == 401
