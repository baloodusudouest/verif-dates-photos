"""Connexion par mot de passe unique et cookie de session signé (HMAC).

Repris du socle (lui-même repris de carnet). Changer le mot de passe déconnecte tous les navigateurs ;
« Se déconnecter » change la génération enregistrée : tous les cookies émis avant
deviennent invalides.
"""
from __future__ import annotations

import hashlib
import hmac
import time

DUREE = 30 * 86400
COMMUN = "maison_session"   # cookie commun aux applis de la maison, posé par l'Anneau


class Sessions:
    def __init__(self, appli: str, mot_de_passe: str, generation: int = 0):
        self.cookie = f"{appli}_session"
        self._mdp = mot_de_passe.encode()
        self._cle = hashlib.sha256(f"{appli}-session|".encode() + self._mdp).digest()
        self.generation = generation
        self._echecs: list[float] = []

    def attente(self) -> float:
        """1 s avant de répondre, 5 s après 5 échecs en 5 minutes."""
        t = time.monotonic()
        self._echecs = [x for x in self._echecs if t - x < 300]
        return 5.0 if len(self._echecs) >= 5 else 1.0

    def verifier_mot_de_passe(self, propose: str) -> bool:
        ok = hmac.compare_digest((propose or "").encode(), self._mdp)
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
        return (hmac.compare_digest(signature, self._signature(corps)) and generation == str(self.generation)
                and expire.isdigit() and int(expire) > time.time())

    # ------------------------------------------------------------ connexion commune (posée par l'Anneau)
    # L'Anneau pose à sa connexion un cookie « maison_session », signé avec le mot de passe commun.
    # Le navigateur l'envoie à toutes les applis servies par la même adresse, quel que soit le port :
    # une appli qui a le même mot de passe l'accepte, et s'ouvre sans rien demander. Il ne porte pas de
    # génération : il vaut trente jours, ou jusqu'à ce que le mot de passe change.
    def _signature_commune(self, corps: str) -> str:
        return hmac.new(hashlib.sha256(b"maison-session|" + self._mdp).digest(), corps.encode(), hashlib.sha256).hexdigest()

    def creer_commun(self) -> str:
        expire = str(int(time.time()) + DUREE)
        return f"{expire}.{self._signature_commune(expire)}"

    def commun_valide(self, jeton: str | None) -> bool:
        expire, _, signature = (jeton or "").partition(".")
        return (expire.isdigit() and int(expire) > time.time()
                and hmac.compare_digest(signature, self._signature_commune(expire)))

    def connecte(self, cookies) -> bool:
        """Connecté par le cookie de l'appli, ou par le cookie commun posé par l'Anneau."""
        return self.valide(cookies.get(self.cookie)) or self.commun_valide(cookies.get(COMMUN))
