"""2.0.2 : l'icône et le manifeste se lisent sans session ; rien d'autre ne s'ouvre par ce chemin."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from outil import charger  # noqa: E402


def test_icone_sans_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIF_PASSWORD", "mot-de-passe-essai")
    module = charger()
    c = TestClient(module.app)                        # client nu : aucune session
    for nom, sorte in (("icone.svg", "image/svg+xml"), ("icone-180.png", "image/png"), ("manifest.webmanifest", "application/manifest+json")):
        r = c.get("/" + nom)
        assert r.status_code == 200 and r.headers["content-type"].startswith(sorte), nom
    for code in ("/main.py", "/app/main.py", "/../app/main.py", "/reglages.py"):
        assert "VERIF_PASSWORD" not in c.get(code).text, code      # le code du serveur ne se lit jamais
    assert c.get("/api/etat").status_code == 401      # le reste demande toujours la session
    assert "Éphéméride" in c.get("/manifest.webmanifest").text
