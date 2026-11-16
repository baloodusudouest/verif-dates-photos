"""Module commun des applis maison — contrat 1 (16/11/2026).

Fichier de référence : baloodusudouest/socle, app/maison.py. Il est copié TEL QUEL dans chaque appli
(tools/diffuser_maison.py) ; ne jamais le modifier ailleurs que dans le socle. La dernière ligne porte une
empreinte : le test de chaque appli la recalcule et refuse une copie retouchée.

Variables communes de la stack (aucune valeur dans le code) :
  MAISON_PASSWORD        mot de passe commun des applis ; repli : l'ancienne variable de l'appli
  MAISON_JETON           jeton des échanges entre applis maison (Home Assistant et le PC gardent les leurs)
  MAISON_JETON_ANCIEN    accepté en plus pendant un changement de jeton ; vide le reste du temps
  MAISON_LECTURE_SEULE   « oui » : aucune appli n'écrit chez un service extérieur ; la variable propre à une appli,
                         si elle est posée (« oui » ou « non »), l'emporte pour cette appli
  MAISON_ANNEAU_URL      adresse de l'Anneau dans la stack (défaut http://anneau:8094), pour l'annuaire

Bibliothèque standard seulement : aucune dépendance ajoutée aux applis.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

CONTRAT = 1
VRAI = {"oui", "1", "true", "yes", "vrai"}
FAUX = {"non", "0", "false", "no", "faux"}


def _env(nom: str) -> str:
    return os.environ.get(nom, "").strip() if nom else ""


# ---------------------------------------------------------------- mot de passe commun

def mot_de_passe(*anciens: str) -> str:
    """MAISON_PASSWORD, sinon la première ancienne variable remplie (ex. « CARNET_PASSWORD »)."""
    for nom in ("MAISON_PASSWORD", *anciens):
        if _env(nom):
            return _env(nom)
    return ""


# ---------------------------------------------------------------- lecture seule

def lecture_seule(propre: str = "") -> bool:
    """La variable propre à l'appli l'emporte si elle dit oui ou non ; sinon MAISON_LECTURE_SEULE."""
    v = _env(propre).lower()
    if v in VRAI:
        return True
    if v in FAUX:
        return False
    return _env("MAISON_LECTURE_SEULE").lower() in VRAI


def origine_lecture_seule(propre: str = "") -> str:
    """Pour l'écran Réglages : « appli », « maison » ou « » (écriture permise)."""
    v = _env(propre).lower()
    if v in VRAI:
        return "appli"
    if v in FAUX:
        return ""
    return "maison" if _env("MAISON_LECTURE_SEULE").lower() in VRAI else ""


# ---------------------------------------------------------------- jeton commun

def jetons(*anciens: str) -> tuple[str, ...]:
    """Jetons acceptés : MAISON_JETON, MAISON_JETON_ANCIEN, puis les anciennes variables de l'appli (transition).
    Vide : porte fermée."""
    vus: list[str] = []
    for nom in ("MAISON_JETON", "MAISON_JETON_ANCIEN", *anciens):
        v = _env(nom)
        if v and v not in vus:
            vus.append(v)
    return tuple(vus)


def jeton_a_envoyer(*anciens: str) -> str:
    """Le jeton qu'une appli joint à ses demandes. Pendant la transition, l'ancienne variable de la paire passe
    d'abord (l'appli d'en face ne connaît peut-être pas encore MAISON_JETON) ; une fois les anciennes retirées
    de la stack, c'est MAISON_JETON."""
    for nom in (*anciens, "MAISON_JETON"):
        if _env(nom):
            return _env(nom)
    return ""


def jeton_propose(entetes) -> str:
    """Lit « Authorization: Bearer … » (ou X-Maison-Jeton) dans des en-têtes de requête."""
    brut = (entetes.get("authorization") or entetes.get("Authorization") or "").strip()
    if brut.lower().startswith("bearer "):
        return brut[7:].strip()
    return (entetes.get("x-maison-jeton") or entetes.get("X-Maison-Jeton") or "").strip()


def jeton_valide(propose: str, acceptes) -> bool:
    """Comparaison en temps constant avec chaque jeton accepté ; aucun jeton accepté = refus."""
    if not propose or not acceptes:
        return False
    bon = False
    for j in acceptes:
        bon |= hmac.compare_digest(propose.encode(), j.encode())
    return bon


# ---------------------------------------------------------------- annuaire (adresses des autres services)

def cle(nom: str) -> str:
    """Même règle que l'Anneau : minuscules, sans accents ni espaces (« Douze points » → « douzepoints »)."""
    import unicodedata
    plat = unicodedata.normalize("NFD", nom or "").encode("ascii", "ignore").decode().lower()
    return "".join(c for c in plat if c.isalnum())


def est_prive(hote: str) -> bool:
    """Vrai si la page a été ouverte par le réseau privé (Tailscale : *.ts.net ou 100.64.0.0/10)."""
    h = (hote or "").split(":")[0].lower()
    if h.endswith(".ts.net"):
        return True
    morceaux = h.split(".")
    return len(morceaux) == 4 and morceaux[0] == "100" and morceaux[1].isdigit() and 64 <= int(morceaux[1]) <= 127


class Annuaire:
    """Adresses des services, lues dans l'Anneau (GET /externe/services, jeton commun), gardées dix minutes.
    L'Anneau ne répond pas : dernières adresses connues, puis l'adresse de repli donnée par l'appli."""

    def __init__(self, url: str | None = None, jeton: str | None = None, duree: int = 600, lire=None):
        self.url = (url if url is not None else (_env("MAISON_ANNEAU_URL") or "http://anneau:8094")).rstrip("/")
        self.jeton = jeton if jeton is not None else jeton_a_envoyer()
        self.duree = duree
        self._lire = lire or self._lire_http
        self._services: dict[str, dict] = {}
        self._quand: float | None = None
        self._verrou = threading.Lock()
        self.erreur = ""

    def _lire_http(self) -> list[dict]:
        demande = urllib.request.Request(self.url + "/externe/services",
                                         headers={"Authorization": f"Bearer {self.jeton}"})
        with urllib.request.urlopen(demande, timeout=5) as r:      # noqa: S310 (adresse de la stack)
            return json.loads(r.read().decode())["services"]

    def services(self) -> dict[str, dict]:
        with self._verrou:
            if self.jeton and (self._quand is None or time.monotonic() - self._quand > self.duree):
                try:
                    self._services = {cle(s["nom"]): s for s in self._lire()}
                    self.erreur = ""
                except Exception as exc:                          # noqa: BLE001 — l'annuaire ne doit rien casser
                    self.erreur = f"Anneau injoignable ({exc.__class__.__name__})"
                self._quand = time.monotonic()
            return dict(self._services)

    def adresse(self, nom: str, prive: bool = False, repli: str = "") -> str:
        """Adresse du service « nom » pour le navigateur : privée si la page vient du réseau privé et que
        l'Anneau en connaît une ; sinon la locale ; sinon le repli."""
        s = self.services().get(cle(nom))
        if not s:
            return repli
        return ((s.get("privee") if prive else "") or s.get("locale") or repli).rstrip("/")


# ---------------------------------------------------------------- empreinte (copie non retouchée)

def empreinte_calculee(chemin: str | Path | None = None) -> str:
    texte = Path(chemin or __file__).read_text(encoding="utf-8")
    corps = texte.rsplit("\n# empreinte: ", 1)[0]
    return hashlib.sha256(corps.encode("utf-8")).hexdigest()[:16]


def empreinte_ecrite(chemin: str | Path | None = None) -> str:
    texte = Path(chemin or __file__).read_text(encoding="utf-8")
    return texte.rsplit("\n# empreinte: ", 1)[-1].strip() if "\n# empreinte: " in texte else ""
# empreinte: b7c1d8273b5b4173
