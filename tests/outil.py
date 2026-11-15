"""Accès des tests aux modules d'Éphéméride (2.2.0 : l'ancien fichier unique est découpé en modules).

`charger()` relit l'environnement (rechargement des modules dans l'ordre de leurs dépendances) et rend
un objet qui se comporte comme l'ancien module unique : `outil.STATE`, `outil.scan_worker`… Remplacer un
nom (`outil.run_exiftool_files = …`, `monkeypatch.setattr(outil, "ORIGINALS_DIR", …)`) le remplace dans
le module qui le définit : les modules s'appellent entre eux par le module (`X.run_exiftool_files`),
donc le remplacement se voit partout."""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ORDRE = ["reglages", "etat", "dates", "exif", "immich", "analyse", "actions", "web"]


class Outil:
    def __init__(self, modules):
        object.__setattr__(self, "_modules", modules)

    def __getattr__(self, nom):
        for m in reversed(self._modules):
            if nom in vars(m):
                return vars(m)[nom]
        raise AttributeError(nom)

    def __setattr__(self, nom, valeur):
        cibles = [m for m in self._modules if nom in vars(m)] or [self._modules[-1]]
        for m in cibles:
            setattr(m, nom, valeur)


def charger(recharger=True):
    import app
    if recharger:
        importlib.reload(app)
    modules = []
    for nom in ORDRE:
        m = importlib.import_module(f"app.{nom}")
        modules.append(importlib.reload(m) if recharger else m)
    return Outil(modules)
