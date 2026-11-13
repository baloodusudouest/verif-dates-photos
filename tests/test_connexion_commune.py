"""2.1.0 : le cookie commun posé par l'Anneau ouvre l'appli si le mot de passe est le même."""
import hashlib
import hmac
import importlib
import sys
import time
from pathlib import Path

from flask.testing import FlaskClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
MDP = "mot-de-passe-essai"


def cookie(mot_de_passe, expire):
    cle = hashlib.sha256(b"maison-session|" + mot_de_passe.encode()).digest()
    return f"{expire}.{hmac.new(cle, str(expire).encode(), hashlib.sha256).hexdigest()}"


def test_connexion_commune(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIF_PASSWORD", MDP)
    import app as module
    module = importlib.reload(module)
    dans_une_heure = int(time.time()) + 3600

    def etat(valeur):
        c = FlaskClient(module.app)
        if valeur is not None:
            c.set_cookie("maison_session", valeur)
        return c.get("/").status_code

    assert etat(None) == 302                                         # renvoi vers la page du mot de passe
    assert etat(cookie(MDP, dans_une_heure)) == 200
    assert etat(cookie("un-autre-mot-de-passe", dans_une_heure)) == 302
    assert etat(cookie(MDP, int(time.time()) - 5)) == 302
    for faux in ("", ".", "abc", "123.", "1.2.3"):
        assert etat(faux) == 302
    c = FlaskClient(module.app)
    c.set_cookie("maison_session", cookie(MDP, dans_une_heure))
    assert c.get("/connexion").status_code == 302                    # déjà connecté : pas de page de mot de passe
    c.post("/deconnexion")
    assert c.get("/api/state").status_code == 401                    # « Se déconnecter » retire le cookie commun
