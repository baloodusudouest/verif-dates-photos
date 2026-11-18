"""3.3.0 : santé des tâches de fond dans /health (socle 0.9.0).

Tâches suivies, toutes à la demande : « analyse », « corrections », « realignement ». Pas de base SQLite :
copie_base vaut null."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dates import MDP, app  # noqa: E402,F401 — fixture commune

COMMUN = "jeton-commun-essai"


def _tache(corps, nom):
    return next(t for t in corps["taches"] if t["nom"] == nom)


def test_health_porte_la_sante_sans_message_sans_jeton(app, monkeypatch):
    monkeypatch.setenv("MAISON_JETON", COMMUN)
    anonyme = TestClient(app.app)
    corps = anonyme.get("/health").json()
    assert corps["ok"] is True and corps["version"] == app.__version__     # rien d'existant ne change
    assert corps["version_socle"] == "0.9.1"
    assert corps["copie_base"] is None                                     # pas de base SQLite
    noms = {t["nom"]: t for t in corps["taches"]}
    for nom in ("analyse", "corrections", "realignement"):
        assert noms[nom]["type"] == "demande" and noms[nom]["periode_s"] is None
    assert all("message" not in t for t in corps["taches"])
    assert all("message" not in t for t in
               anonyme.get("/health", headers={"Authorization": "Bearer faux"}).json()["taches"])
    avec = anonyme.get("/health", headers={"Authorization": f"Bearer {COMMUN}"}).json()
    assert all("message" in t for t in avec["taches"])
    connecte = app.app.test_client()
    assert all("message" in t for t in connecte.get("/health").json()["taches"])


def test_analyse_ratee_passe_en_erreur_et_reussie_en_ok(app, monkeypatch):
    monkeypatch.setenv("MAISON_JETON", COMMUN)

    def casse(on_progress=None):
        raise OSError("disque débranché")

    monkeypatch.setattr(app.module_de("list_files"), "list_files", casse)
    app.scan_worker("tout")                                                # n'élève rien, comme avant
    assert app.STATE["error"] == "disque débranché"                        # comportement d'avant inchangé
    porteur = {"Authorization": f"Bearer {COMMUN}"}
    t = _tache(TestClient(app.app).get("/health", headers=porteur).json(), "analyse")
    assert t["etat"] == "erreur" and t["message"] == "OSError: disque débranché"

    monkeypatch.setattr(app.module_de("list_files"), "list_files", lambda on_progress=None: [])
    app.scan_worker("tout")
    assert _tache(TestClient(app.app).get("/health").json(), "analyse")["etat"] == "ok"
