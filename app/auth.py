"""Connexion par mot de passe unique et cookie de session signé (HMAC).

Repris du socle (lui-même repris de carnet). Changer le mot de passe déconnecte tous les navigateurs ;
« Se déconnecter » change la génération enregistrée : tous les cookies émis avant
deviennent invalides.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import time

from . import maison

DUREE = 30 * 86400
COMMUN = "maison_session"   # cookie commun aux applis de la maison, posé par l'Anneau


ESSAIS_MAX = 30          # essais de mot de passe en 5 minutes au-delà desquels tout est refusé sans comparer


@functools.lru_cache(maxsize=32)
def _cle_lente(prefixe: bytes, mot_de_passe: bytes) -> bytes:
    """Clé de signature tirée du mot de passe par PBKDF2 (200 000 tours) : calculée une fois au démarrage,
    elle rend la recherche du mot de passe depuis un cookie capté des centaines de milliers de fois plus lente."""
    return hashlib.pbkdf2_hmac("sha256", mot_de_passe, prefixe, 200_000)


def _egal(a: str, b: str) -> bool:
    """Comparaison en temps constant, en octets : une valeur non ASCII ne lève plus d'erreur."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


class Sessions:
    def __init__(self, appli: str, mot_de_passe: str, generation: int = 0):
        self.cookie = f"{appli}_session"
        self._mdp = mot_de_passe.encode()
        self._cle = _cle_lente(f"{appli}-session|".encode(), self._mdp)
        self._cle_commune = _cle_lente(b"maison-session|", self._mdp)
        self._essais: list[float] = []
        self.generation = generation
        self._echecs: list[float] = []

    def attente(self) -> float:
        """1 s avant de répondre, 5 s après 5 échecs en 5 minutes."""
        t = time.monotonic()
        self._echecs = [x for x in self._echecs if t - x < 300]
        self._essais = [x for x in self._essais if t - x < 300]
        self._essais.append(t)                     # compté dès l'arrivée : des essais en parallèle ne se cachent plus
        return 5.0 if len(self._echecs) >= 5 or len(self._essais) > 5 else 1.0

    def verifier_mot_de_passe(self, propose: str) -> bool:
        t = time.monotonic()
        if len([x for x in self._essais if t - x < 300]) > ESSAIS_MAX:
            ok = False                             # trop d'essais : refus sans comparer
        else:
            ok = hmac.compare_digest((propose or "").encode(), self._mdp)
        if ok:
            self._essais.clear()
        if not ok:
            self._echecs.append(time.monotonic())
        return ok

    def _signature(self, corps: str) -> str:
        return hmac.new(self._cle, corps.encode(), hashlib.sha256).hexdigest()

    def creer(self) -> str:
        corps = f"{self.generation}.{int(time.time()) + DUREE}"
        return f"{corps}.{self._signature(corps)}"

    def valide(self, jeton: str | None) -> bool:
        try:
            generation, expire, signature = (jeton or "").split(".")
        except ValueError:
            return False
        corps = f"{generation}.{expire}"
        return (_egal(signature, self._signature(corps)) and generation == str(self.generation)
                and expire.isdigit() and int(expire) > time.time())

    # ------------------------------------------------------------ connexion commune (posée par l'Anneau)
    # L'Anneau pose à sa connexion un cookie « maison_session », signé par une clé tirée du mot de passe commun
    # (PBKDF2 depuis l'audit du 17/11/2026 : toutes les applis doivent être à jour ensemble).
    # Le navigateur l'envoie à toutes les applis servies par la même adresse, quel que soit le port :
    # une appli qui a le même mot de passe l'accepte, et s'ouvre sans rien demander. Il ne porte pas de
    # génération : il vaut trente jours, ou jusqu'à ce que le mot de passe change.
    def _signature_commune(self, corps: str) -> str:
        return hmac.new(self._cle_commune, corps.encode(), hashlib.sha256).hexdigest()

    def creer_commun(self, generation: int | None = None) -> str:
        """Cookie commun « génération.expiration.signature » (la génération est tenue par l'Anneau :
        « Se déconnecter » dans l'Anneau l'augmente, et les cookies communs d'avant ne valent plus nulle part)."""
        g = generation if generation is not None else (maison.generation_commune() or 0)
        corps = f"{g}.{int(time.time()) + DUREE}"
        return f"{corps}.{self._signature_commune(corps)}"

    def commun_valide(self, jeton: str | None) -> bool:
        try:
            generation, expire, signature = (jeton or "").split(".")
        except ValueError:
            return False
        if not (generation.isdigit() and expire.isdigit() and int(expire) > time.time()
                and _egal(signature, self._signature_commune(f"{generation}.{expire}"))):
            return False
        actuelle = maison.generation_commune()        # None : l'Anneau ne répond pas, le cookie reste accepté
        return actuelle is None or int(generation) == actuelle

    def connecte(self, cookies) -> bool:
        """Connecté par le cookie de l'appli, ou par le cookie commun posé par l'Anneau."""
        return self.valide(cookies.get(self.cookie)) or self.commun_valide(cookies.get(COMMUN))
