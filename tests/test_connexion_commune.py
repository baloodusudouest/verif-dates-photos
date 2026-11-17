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


def cookie(mot_de_passe, expire, generation=0):
    cle = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), b"maison-session|", 200_000)   # contrat commun depuis le 17/11/2026
    corps = f"{generation}.{expire}"            # « génération.expiration.signature » depuis le 18/11/2026
    return f"{corps}.{hmac.new(cle, corps.encode(), hashlib.sha256).hexdigest()}"


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


def test_se_deconnecter_dans_l_anneau_revoque_le_cookie_commun(monkeypatch):
    """Audit du 17/11/2026 : la génération commune tenue par l'Anneau change, les cookies communs d'avant ne valent plus."""
    monkeypatch.setenv("MAISON_PASSWORD", MDP)
    monkeypatch.delenv("MAISON_JETON", raising=False)
    charger()
    from app import maison
    from app.auth import COMMUN, Sessions
    s = Sessions("essai", MDP)
    dans_une_heure = int(time.time()) + 3600
    monkeypatch.setattr(maison, "generation_commune", lambda: 3)
    assert s.connecte({COMMUN: cookie(MDP, dans_une_heure, 3)})
    assert not s.connecte({COMMUN: cookie(MDP, dans_une_heure, 2)})                        # d'avant la déconnexion
    assert not s.connecte({COMMUN: cookie(MDP, dans_une_heure, 2).replace("2.", "3.", 1)})  # génération retouchée
    assert s.connecte({COMMUN: s.creer_commun()})                                           # nouveau cookie : génération 3
    monkeypatch.setattr(maison, "generation_commune", lambda: None)                         # Anneau injoignable
    assert s.connecte({COMMUN: cookie(MDP, dans_une_heure, 2)})
