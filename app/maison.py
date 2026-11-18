"""Module commun des applis maison — contrat 2 (16/11/2026 ; contrat 1 : variables communes, contrat 2 : réglages et sauvegarde).

Fichier de référence : baloodusudouest/socle, app/maison.py. Il est copié TEL QUEL dans chaque appli
(tools/diffuser_maison.py) ; ne jamais le modifier ailleurs que dans le socle. La dernière ligne porte une
empreinte : le test de chaque appli la recalcule et refuse une copie retouchée.

Variables communes de la stack (aucune valeur dans le code) :
  MAISON_PASSWORD        mot de passe commun des applis ; repli : l'ancienne variable de l'appli
  MAISON_JETON           jeton des échanges entre applis maison (Home Assistant et le PC gardent les leurs)
  MAISON_JETON_ANCIEN    accepté en plus pendant un changement de jeton ; vide le reste du temps
  MAISON_LECTURE_SEULE   « oui » : aucune appli n'écrit chez un service extérieur ; la variable propre à une appli,
                         si elle est posée (« oui » ou « non »), l'emporte pour cette appli
  MAISON_ANNEAU_URL      adresse de l'Anneau dans la stack (défaut http://anneau:8094), pour l'annuaire et les sauvegardes

Sauvegarde des réglages (contrat de sauvegarde 1) : chaque appli garde ses réglages d'usage (classe Reglages, ou sa
propre table) et les rend par GET /externe/sauvegarde (jeton commun) avec sauvegarde(...) ; l'Anneau les collecte
chaque nuit ; « Restaurer » relit une sauvegarde chez l'Anneau (lire_sauvegarde) et applique les différences.
Jamais de secret dans une sauvegarde : est_secret() écarte tout nom qui y ressemble, et chaque appli peut en exclure
d'autres explicitement.

Bibliothèque standard seulement : aucune dépendance ajoutée aux applis.
"""
from __future__ import annotations

import hashlib
import hmac
import contextlib
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

CONTRAT = 2
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


_ANNUAIRE: "Annuaire | None" = None
AGE_GENERATION = 60                 # secondes : la génération commune est relue au plus toutes les minutes
_FOURNISSEUR_GENERATION = None      # l'Anneau donne la sienne directement (definir_generation_commune)


def definir_generation_commune(fournisseur) -> None:
    """0.8.0 : pour l'Anneau, qui tient la génération lui-même au lieu de la lire dans son annuaire."""
    global _FOURNISSEUR_GENERATION
    _FOURNISSEUR_GENERATION = fournisseur


def generation_commune() -> int | None:
    """0.8.0 : génération actuelle du cookie commun « maison_session » ; None si inconnue (l'Anneau ne répond
    pas, ou pas de jeton commun) : le cookie est alors accepté comme avant, pour ne rien casser."""
    if _FOURNISSEUR_GENERATION is not None:
        return _FOURNISSEUR_GENERATION()
    annuaire = _ANNUAIRE
    if annuaire is None:
        if not jeton_a_envoyer():
            return None
        annuaire = Annuaire()
    return annuaire.generation_sans_attendre()


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
        self.generation_commune: int | None = None     # 0.8.0 : génération du cookie commun, publiée par l'Anneau
        global _ANNUAIRE
        _ANNUAIRE = self

    def _lire_http(self) -> dict:
        demande = urllib.request.Request(self.url + "/externe/services",
                                         headers={"Authorization": f"Bearer {self.jeton}"})
        with urllib.request.urlopen(demande, timeout=5) as r:      # noqa: S310 (adresse de la stack)
            return json.loads(r.read().decode())          # {"services": [...], "generation_commune": n}

    def services(self, age_max: float | None = None) -> dict[str, dict]:
        limite = self.duree if age_max is None else age_max
        with self._verrou:
            if self.jeton and (self._quand is None or time.monotonic() - self._quand > limite):
                try:
                    lu = self._lire()
                    if isinstance(lu, dict):
                        g = lu.get("generation_commune")
                        self.generation_commune = g if isinstance(g, int) and not isinstance(g, bool) else None
                        lu = lu.get("services") or []
                    self._services = {cle(s["nom"]): s for s in lu}
                    self.erreur = ""
                    self._quand = time.monotonic()
                except Exception as exc:                          # noqa: BLE001 — l'annuaire ne doit rien casser
                    self.erreur = f"Anneau injoignable ({exc.__class__.__name__})"
                    # nouvel essai 30 s plus tard (avant : 10 min sans adresses après un redémarrage de la stack)
                    self._quand = time.monotonic() - max(0.0, limite - 30)
            return dict(self._services)

    def generation_sans_attendre(self) -> int | None:
        """0.8.0 : dernière génération connue, sans jamais faire attendre une requête : si elle est trop vieille,
        elle est relue en arrière-plan (au plus une minute de retard après « Se déconnecter » dans l'Anneau).
        None tant qu'on ne la connaît pas (l'Anneau ne répond pas)."""
        age_max = min(self.duree, AGE_GENERATION)
        if self.jeton and (self._quand is None or time.monotonic() - self._quand > age_max) \
                and not self._verrou.locked():
            threading.Thread(target=self.services, args=(age_max,), daemon=True, name="annuaire").start()
        return self.generation_commune

    def adresse(self, nom: str, prive: bool = False, repli: str = "") -> str:
        """Adresse du service « nom » pour le navigateur : privée si la page vient du réseau privé et que
        l'Anneau en connaît une ; sinon la locale ; sinon le repli."""
        s = self.services().get(cle(nom))
        if not s:
            return repli
        return ((s.get("privee") if prive else "") or s.get("locale") or repli).rstrip("/")


# ---------------------------------------------------------------- réglages d'usage (contrat 2)

CONTRAT_SAUVEGARDE = 1
# 0.8.2 (audit, M3) : par mots du nom et non plus par morceau (« code_postal » et « mot_cle » ne sont plus écartés
# à tort ; « mdp », « pwd », « pass » le sont désormais).
_MOTS_SECRETS = {"password", "passwd", "pass", "pwd", "mdp", "motdepasse", "secret", "jeton", "token", "cle", "key",
                 "apikey", "code", "hash", "webhook", "identifiant", "login", "user", "username", "email", "courriel",
                 "dsn", "auth", "authorization", "cookie", "session", "private", "prive"}
_DANS_LE_NOM = ("password", "motdepasse", "apikey", "webhook", "secret", "token", "jeton")   # même collés (« monTokenX »)
_INOFFENSIFS = {("code", "postal"), ("code", "pays"), ("code", "couleur"), ("code", "langue"), ("mot", "cle"),
                ("cle", "tri")}
_URL_AVEC_IDENTIFIANTS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def _mots(nom: str) -> list[str]:
    import unicodedata
    plat = unicodedata.normalize("NFD", nom or "").encode("ascii", "ignore").decode()
    plat = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", plat).lower()
    return [m for m in re.split(r"[^a-z0-9]+", plat) if m]


def est_secret(nom: str) -> bool:
    """Même esprit que la règle d'Atlas : tout nom qui ressemble à un secret est écarté des sauvegardes."""
    mots = _mots(nom)
    paires = set(zip(mots, mots[1:]))
    for i, m in enumerate(mots):
        if m in _MOTS_SECRETS and not any(p in _INOFFENSIFS for p in paires if m in p):
            return True
    colle = "".join(mots)
    return any(m in colle for m in _DANS_LE_NOM)


def valeur_secrete(v) -> bool:
    """0.8.2 : une valeur qui porte des identifiants (adresse « https://nom:motdepasse@hote ») ne sort jamais."""
    return isinstance(v, str) and bool(_URL_AVEC_IDENTIFIANTS.search(v))


def _convertir(brut: str, modele):
    """Valeur d'une variable (texte) vers le type de la valeur par défaut."""
    if isinstance(modele, bool):
        return brut.strip().lower() in VRAI
    if isinstance(modele, int):
        return int(brut.strip())
    if isinstance(modele, float):
        return float(brut.strip().replace(",", "."))
    if isinstance(modele, list):
        sep = "|" if "|" in brut else ","
        return [x.strip() for x in brut.split(sep) if x.strip()]
    return brut.strip()


def _meme_type(valeur, modele):
    """Accepte une valeur reçue de l'écran ou d'une sauvegarde si elle a le type de la valeur par défaut."""
    if isinstance(modele, bool):
        if isinstance(valeur, bool):
            return valeur
        raise ValueError("oui ou non attendu")
    if isinstance(modele, int) and not isinstance(modele, bool):
        if isinstance(valeur, bool):
            raise ValueError("nombre entier attendu")
        if isinstance(valeur, float) and (not math.isfinite(valeur) or valeur != int(valeur)):
            raise ValueError("nombre entier attendu")
        try:
            return int(valeur)
        except OverflowError as exc:
            raise ValueError("nombre entier attendu") from exc
    if isinstance(modele, float):
        v = float(valeur)
        if not math.isfinite(v):           # nan ou inf cassaient l'écran et la sauvegarde pour de bon
            raise ValueError("nombre fini attendu")
        return v
    if isinstance(modele, list):
        if isinstance(valeur, str):
            return _convertir(valeur, modele)
        if isinstance(valeur, list):
            return [str(x).strip() for x in valeur if str(x).strip()]
        raise ValueError("liste attendue")
    if isinstance(valeur, (dict, list)):
        raise ValueError("texte attendu")
    return "" if valeur is None else str(valeur)


class Reglages:
    """Réglages d'usage d'une appli, gardés dans un fichier JSON de son dossier de données.

    defauts   : {clé: valeur par défaut} — le type de la valeur par défaut fixe le type du réglage ;
    variables : {clé: NOM_DE_VARIABLE} — au premier démarrage, une clé absente du fichier prend la valeur de sa
                variable si elle est posée (reprise depuis la stack), sinon la valeur par défaut. Ensuite la variable
                n'est plus lue : le réglage se change dans l'écran Réglages.
    """

    def __init__(self, chemin: str | Path, defauts: dict, variables: dict | None = None):
        self.chemin = Path(chemin)
        self.defauts = dict(defauts)
        self._verrou = threading.Lock()
        self.repris: list[str] = []          # clés reprises d'une variable à ce démarrage (pour le journal)
        donnees: dict = {}
        try:
            donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
            if not isinstance(donnees, dict):
                donnees = {}
        except ValueError:
            # fichier abîmé : mis de côté (.corrompu) au lieu d'être écrasé sans copie
            with contextlib.suppress(OSError):
                os.replace(self.chemin, self.chemin.with_suffix(".corrompu"))
            donnees = {}
        except OSError:
            donnees = {}
        change = False
        for k, defaut in self.defauts.items():
            if k in donnees:
                try:
                    donnees[k] = _meme_type(donnees[k], defaut)
                except (TypeError, ValueError):
                    donnees[k] = defaut
                    change = True
                continue
            brut = _env((variables or {}).get(k, ""))
            valeur = defaut
            if brut:
                try:
                    valeur = _convertir(brut, defaut)
                    self.repris.append(k)
                except (TypeError, ValueError):
                    valeur = defaut
            donnees[k] = valeur
            change = True
        self._donnees = {k: donnees[k] for k in self.defauts}
        if change:
            self._ecrire()

    def _ecrire(self) -> None:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.chemin.with_suffix(self.chemin.suffix + ".tmp")
        tmp.write_text(json.dumps(self._donnees, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.chemin)

    def tout(self) -> dict:
        with self._verrou:
            return json.loads(json.dumps(self._donnees))

    def get(self, k: str):
        with self._verrou:
            return json.loads(json.dumps(self._donnees[k]))

    def changer(self, valeurs: dict) -> dict:
        """Change les clés connues (les autres sont ignorées) ; ValueError si une valeur n'a pas le bon type."""
        propres = {}
        for k, v in (valeurs or {}).items():
            if k in self.defauts:
                propres[k] = _meme_type(v, self.defauts[k])
        with self._verrou:
            self._donnees.update(propres)
            self._ecrire()
            return json.loads(json.dumps(self._donnees))


# ---------------------------------------------------------------- sauvegarde et restauration (contrat de sauvegarde 1)

def _json_simple(v) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def reglages_publics(reglages: dict, exclus=()) -> dict:
    """Les réglages qui peuvent sortir de l'appli : ni nom de secret, ni clé exclue par l'appli."""
    ex = {cle(e) for e in exclus}
    return {k: v for k, v in (reglages or {}).items()
            if not est_secret(k) and cle(k) not in ex and _json_simple(v) and not valeur_secrete(v)}


def reglages_ecartes(reglages: dict, exclus=()) -> list[str]:
    """0.8.2 (M3) : les noms des réglages écartés comme secrets (jamais leur valeur), pour le dire à l'écran.
    Les réglages exclus exprès par l'appli (« exclus ») n'y figurent pas : ils ne sont pas à sauvegarder."""
    publics = reglages_publics(reglages, exclus)
    return sorted(k for k in (reglages or {}) if k not in publics and k not in set(exclus))


def sauvegarde(appli: str, version: str, reglages: dict, exclus=()) -> dict:
    """Réponse de GET /externe/sauvegarde : réglages et version seulement, jamais de variable ni de secret."""
    return {"contrat": CONTRAT_SAUVEGARDE, "appli": appli, "version": version,
            "le": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reglages": reglages_publics(reglages, exclus), "ecartes": reglages_ecartes(reglages, exclus)}


def differences(actuels: dict, sauves: dict, exclus=()) -> list[dict]:
    """Réglages d'une sauvegarde qui diffèrent de l'actuel, pour les clés que l'appli connaît encore."""
    s = reglages_publics(sauves, exclus)
    return [{"cle": k, "actuel": actuels.get(k), "sauve": v}
            for k, v in sorted(s.items()) if k in actuels and actuels.get(k) != v]


def lire_sauvegarde(appli: str, le: str = "", url: str | None = None, jeton: str | None = None, lire=None) -> dict:
    """Demande à l'Anneau (jeton commun) : sans « le », {"dates": ["AAAA-MM-JJ", …]} (la plus récente d'abord) ;
    avec « le », {"le", "version", "reglages"} de cette appli ce jour-là. Lève une exception si l'Anneau ne répond
    pas ou n'a rien : l'appli l'affiche telle quelle."""
    base = (url if url is not None else (_env("MAISON_ANNEAU_URL") or "http://anneau:8094")).rstrip("/")
    adresse = f"{base}/externe/sauvegardes/{urllib.parse.quote(appli)}"
    if le:
        adresse += "?le=" + urllib.parse.quote(le)
    jet = jeton if jeton is not None else jeton_a_envoyer()
    if lire is not None:
        return lire(adresse, jet)
    demande = urllib.request.Request(adresse, headers={"Authorization": f"Bearer {jet}"})
    with urllib.request.urlopen(demande, timeout=10) as r:          # noqa: S310 (adresse de la stack)
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- empreinte (copie non retouchée)

def empreinte_calculee(chemin: str | Path | None = None) -> str:
    texte = Path(chemin or __file__).read_text(encoding="utf-8")
    corps = texte.rsplit("\n# empreinte: ", 1)[0]
    return hashlib.sha256(corps.encode("utf-8")).hexdigest()[:16]


def empreinte_ecrite(chemin: str | Path | None = None) -> str:
    texte = Path(chemin or __file__).read_text(encoding="utf-8")
    return texte.rsplit("\n# empreinte: ", 1)[-1].strip() if "\n# empreinte: " in texte else ""
# empreinte: be8c00101be6d7ee
