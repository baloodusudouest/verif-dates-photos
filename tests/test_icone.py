"""2.0.2 : l'icône et le manifeste se lisent sans session ; rien d'autre ne s'ouvre par ce chemin."""
import importlib
import sys
from pathlib import Path

from flask.testing import FlaskClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


def test_icone_sans_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERIF_PASSWORD", "mot-de-passe-essai")
    import app as module
    module = importlib.reload(module)
    c = FlaskClient(module.app)                       # client nu : aucune session
    for nom, sorte in (("icone.svg", "image/svg+xml"), ("icone-180.png", "image/png"), ("manifest.webmanifest", "application/manifest+json")):
        r = c.get("/" + nom)
        assert r.status_code == 200 and r.mimetype == sorte, nom
    assert c.get("/app.py").status_code == 404
    assert c.get("/api/state").status_code == 401     # le reste demande toujours la session
    assert "Éphéméride" in c.get("/manifest.webmanifest").get_data(as_text=True)
