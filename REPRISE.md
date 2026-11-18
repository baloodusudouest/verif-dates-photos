# Reprise — Éphéméride (vérification des dates)

Nom d'appli depuis la 2.0.1 : **Éphéméride**. Noms techniques inchangés (`verif-dates-photos`, `VERIF_LECTURE_SEULE`).

Fichier à lire en premier dans une nouvelle session, et à mettre à jour à chaque fin de session, dans le même commit que le code.

## État (16/11/2026)

- **3.3.0** (18/11/2026, santé des tâches de fond, socle 0.9.0) : `/health` fusionne `maison.sante(...)` (`version_socle`, `taches`, `copie_base` = null : pas de base). Tâches à la demande dans `maison.TACHES` : `analyse` (`analyse.scan_worker`), `corrections` (`actions.apply_worker`), `realignement` (`actions.realign_worker`), déclarées par `main.declarer_taches`. Le registre vit en mémoire : **un seul worker uvicorn** (c'est déjà le cas dans le Dockerfile). Dans les tests, remplacer un nom par `monkeypatch.setattr(app.module_de("NOM"), "NOM", …)`. Jamais essayé sur la vraie installation.
- **3.2.5** (18/11/2026, constats moyens de l'audit) : délais d'exiftool (`exif.DELAI_LECTURE`, `DELAI_LOT`, `DELAI_ECRITURE`, exception `exif.Delai`) ; copie de chaque photo avant sa première date dans `R.AVANT_DATE_DIR` (`/data/originaux-avant-date`, `exif.copier_avant_date`) ; `save_validated` et `save_results` écrivent sous `LOCK`. Les tests ont besoin d'exiftool (sinon sautés). **Jamais essayé sur la vraie installation** ; surveiller la place prise par `/data/originaux-avant-date`.
- **3.2.0** (16/11/2026) : réglages dans l'appli, sauvegarde et restauration ; anciens noms retirés. **Jamais essayé sur la vraie installation.**
  - `app/maison.py` : contrat 2 du socle, copié tel quel (test d'empreinte).
  - `app/reglages.py` : `PASSWORD = maison.mot_de_passe()` sans ancien nom ; moins de 12 caractères → `SystemExit` (« MAISON_PASSWORD est absent ou trop court… »), l'outil ne démarre pas. Réglages d'usage `USAGE = maison.Reglages(DATA_DIR/reglages.json, DEFAUTS, ANCIENNES_VARIABLES)` : `sous_dossier` (texte, « Pellicule », repris de `SCAN_SUBDIR`) et `tolerance_jours` (entier 0–366, 1, repris de `TOLERANCE_DAYS`). `verifier()` contrôle les valeurs (pas de `..`, pas de chemin complet), `changer()` enregistre puis `appliquer()` recopie dans `R.SCAN_DIR` et `R.TOLERANCE_DAYS` : le reste du code lit toujours ces deux noms par le module, d'où l'effet immédiat. `EXCLUS_SAUVEGARDE` : clés qui ne sortent jamais (`lecture_seule`, `lecture_seule_origine`, `originaux_gardes`, `dossier_present`). `APPLI_ID = "verif-dates-photos"`.
  - `app/main.py` : `GET/PUT /api/reglages` (session ; 400 si mauvaise valeur ; 409 pendant une tâche), `GET /externe/sauvegarde` (jeton commun seulement, 404 sinon), `GET /api/restaurer` (dates ; `?le=` : différences), `POST /api/restaurer` (applique, journal). Reprises et changements notés dans `corrections.csv` (colonne `statut` = « réglage », `fichier` vide : le réalignement ne lit que les « échec »). Faux Anneau des tests : `ETAT["lire_sauvegarde"]` (argument `lire=` de `maison.lire_sauvegarde`).
  - `frontend/src/pages/Reglages.svelte` : champs Dossier analysé et Tolérance, tuile « Restaurer les réglages » (`#reglages`, `#restaurer`).
  - Variables lues : `MAISON_PASSWORD`, `MAISON_JETON` (et `MAISON_JETON_ANCIEN` pendant un changement de jeton), `MAISON_LECTURE_SEULE`, `MAISON_ANNEAU_URL` (facultative), `VERIF_LECTURE_SEULE`, `LOCAL_ROOT`, `IMMICH_URL`, `IMMICH_API_KEY`, `IMMICH_ROOT` ; fixées par l'image : `TZ`, `DATA_DIR`, `PORT` ; facultative hors stack : `ORIGINALS_DIR`. `SCAN_SUBDIR` et `TOLERANCE_DAYS` : lues une seule fois (reprise). Plus lue : `VERIF_PASSWORD`.
  - **Déploiement en deux temps** : d'abord la nouvelle image avec la stack actuelle (les réglages sont repris des anciennes variables au premier démarrage), vérifier Réglages, puis la nouvelle stack sans les anciennes lignes (`VERIF_PASSWORD`, `SCAN_SUBDIR`, `TOLERANCE_DAYS`) et avec `MAISON_JETON`. Attention : `MAISON_PASSWORD` doit déjà être dans le service (c'est le cas dans la stack du coffre) ; sans lui, la nouvelle image ne démarre pas.
- **3.1.0** (16/11/2026) : variables communes (module `app/maison.py`, copie du socle 0.5.0 : ne pas le modifier ici, `python tools/diffuser_maison.py ../verif-dates-photos` depuis le socle). `app/reglages.py` lit `PASSWORD = maison.mot_de_passe("VERIF_PASSWORD")`, `LECTURE_SEULE = maison.lecture_seule("VERIF_LECTURE_SEULE")` et `ORIGINE_LECTURE_SEULE` (« appli », « maison » ou « »), renvoyée par `/api/session` et `/api/etat` (`lecture_seule_origine`) et affichée dans Réglages, Connexion et le bandeau d'À traiter. Pas de jeton entre applis ni d'annuaire (aucun lien navigateur vers Immich). Jamais essayé sur la vraie installation : les nouvelles variables ne sont pas encore dans la stack (tout passe par `VERIF_PASSWORD` / `VERIF_LECTURE_SEULE`).
- **3.0.0** (15/11/2026) : format du socle. `app/main.py` (FastAPI, garde, routes `/api/…`), `app/auth.py` (repris du socle), interface Svelte dans `frontend/` compilée vers `app/static` (non suivi par git ; l'image la compile). Lancement : `uvicorn app.main:app` (port `PORT`, 8099). `app/web.py` et Flask sont retirés ; ce que faisaient ses routes est dans `actions.py` (`lancer_analyse`, `lancer_corrections`, `lancer_realignement`, `valider_dossier`, `renommer_dossier`, `progression`, `etat_complet`), refus par l'exception `Refus` (message, code HTTP). Version dans `app/__init__.py` **et** `frontend/package.json`.
- **2.2.0** (15/11/2026) : `app/app.py` découpé en modules (`app/reglages.py`, `etat.py`, `dates.py`, `exif.py`, `immich.py`, `analyse.py`, `actions.py`), version dans `app/__init__.py`. Les modules s'appellent **par le module** (`X.write_date`, `R.DATA_DIR`), jamais par `from … import nom` : c'est ce qui permet aux tests (`tests/outil.py`) et au rechargement de remplacer un nom partout.
- **2.1.2** (15/11/2026) : verrou d'écriture (`ECRITURE`, `ecrire_json`, `noter_journal` dans `app/etat.py`) pour les fichiers d'état et le journal. Test `test_ecritures_simultanees`.
- **2.1.1** (13/11/2026) : toujours sombre (blocs `prefers-color-scheme: light` retirés, variante `dark:` partout).
- **2.1.0** (13/11/2026) : connexion commune avec l'Anneau — cookie `maison_session` (accepté ; `Sessions.connecte`, `commun_valide` dans `app/auth.py`, repris tel quel en 3.0.0). Essayée par Julien le 15/11/2026.
- **2.0.0 sur `main`** : habillage commun en bleu ardoise, rail, tuiles à la place du filtre, sections Connexion et Réglages. Serveur, adresses, règles et fichiers d'état inchangés.
- 1.11.0 : mot de passe obligatoire, mode lecture seule.
- Dossier « tel que construit » et écarts avec carnet : [`docs/dossier.md`](docs/dossier.md). Avis du conseil : [`docs/2.0-conseil.md`](docs/2.0-conseil.md).
- Décisions de Julien (11/11) : l'outil **continuera de servir après la migration** ; **voie médiane** (habillage et navigation communs sur le serveur actuel, sans toucher aux adresses qui écrivent) ; l'original d'une vidéo convertie reste supprimé par défaut (`ORIGINALS_DIR` pour le garder) ; couleur bleu ardoise.

## Prochaine étape

Faits par Julien (15/11/2026) : mise en service 2.1.x et connexion commune avec l'Anneau essayées, vidéos de New York réalignées, sauvegarde vérifiée de la Pellicule, dossier témoin contrôlé dans Immich.

1. Mettre la 3.1.0 (ou la 3.0.0) en service : Portainer › Recreate + Re-pull de `verif_dates_photos`, sans toucher aux variables, se reconnecter une fois (ou passer par l'Anneau). Conseillé pour le premier jour : `VERIF_LECTURE_SEULE=oui`, vérifier l'écran sur PC et iPhone, puis retirer la variable.
2. À essayer sur la vraie installation (jamais fait) : une correction réelle depuis la nouvelle interface, le renommage d'un dossier, la conversion d'un vrai `.MTS` de caméscope, la tuile de l'Anneau avec la sonde `/health`.
3. Retour arrière si besoin : étiquette `:3.0.0` (ou `:2.2.0`) dans la stack.
4. 3.2.0, déploiement en deux temps : (a) Recreate + Re-pull avec la stack actuelle, ouvrir Réglages et vérifier « Pellicule » et 1 jour (repris), et la ligne de reprise dans `corrections.csv` ; (b) nouvelle stack : retirer `VERIF_PASSWORD`, `SCAN_SUBDIR`, `TOLERANCE_DAYS`, ajouter `MAISON_JETON=${MAISON_JETON}`, puis vérifier que l'Anneau collecte la sauvegarde et que « Restaurer les réglages » liste ses dates. Jamais essayé sur la vraie installation.

## Décisions tranchées

- L'outil modifie les fichiers de la photothèque : Watchtower désactivé, mise à jour manuelle.
- Les règles de dates et les fonctions d'écriture ne se réécrivent pas à l'occasion d'un changement d'interface.
- Pas de bouton « remettre l'ancienne date » : `corrections.csv` et la sauvegarde suffisent (avis du conseil).
- Rien de personnel dans le dépôt public.
- Toute écriture d'un fichier d'état ou du journal passe par `ecrire_json` / `noter_journal` (verrou `ECRITURE`).
- 3.0.0 (choix de Julien, 15/11) : réécriture FastAPI + Svelte faite malgré l'avertissement de `format.md` § 5 ; parade : mêmes fonctions d'écriture, mêmes fichiers d'état, test d'écran du clic jusqu'au fichier.

## Pièges

- 3.0 : les tests d'écran s'appuient sur `nav.rail a[aria-label]`, `#tuiles .tuile[aria-pressed]`, `.chiffre`, `#f .card`, `[data-folder][data-nom]`, `#prog[hidden]`, `#corrigees .card` : ne pas les renommer.
- 3.0 : les choix faits à l'écran (date, case « Corriger ») vivent dans le navigateur (`frontend/src/lib/etat.svelte.js`) jusqu'au clic sur « Corriger » ; un rechargement de la page les perd, comme en 2.x.
- 3.0 : un message long (chemin) doit passer à la ligne : `overflow-wrap: anywhere` sur `body` dans `app.css`, sinon l'iPhone dézoome et la pilule sort de l'écran.
- Les tests d'écran (`tests/interface/`) créent de vrais fichiers et ont besoin d'exiftool, de Playwright et de l'interface compilée (`cd frontend && npm ci && npx vite build`) ; ils ne tournent pas sur GitHub (sautés), à lancer avant chaque envoi.
- Les tests ont besoin d'exiftool et de ffmpeg ; sans exiftool, la plupart sont sautés sans bruit. Sur GitHub ils tournent dans l'image.
- Le client de test ouvre la session tout seul (`tests/test_dates.py`, fixture `app`) : un test qui veut un visiteur sans session crée un `TestClient(app.app)` nu. Les tests coupent l'attente après un mot de passe faux (`ETAT["attente"] = False`).
- Revenir à une ancienne image ne rend pas les fichiers modifiés.
- 3.1.0 : `MAISON_LECTURE_SEULE=oui` dans la stack met aussi cet outil en lecture seule (sauf `VERIF_LECTURE_SEULE=non`). Les tests effacent `MAISON_PASSWORD` et `MAISON_LECTURE_SEULE` dans leurs fixtures (et `tests/interface/serveur.py`) : un test qui en a besoin les pose lui-même.
- 3.2.0 : sans `MAISON_PASSWORD` (ou avec moins de 12 caractères), l'outil refuse de démarrer (`SystemExit` à l'import de `reglages`) ; `VERIF_PASSWORD` n'est plus lue. Les tests posent `MAISON_PASSWORD` (« essai-ephemeride ») et effacent `VERIF_PASSWORD`, `MAISON_JETON`, `SCAN_SUBDIR`, `TOLERANCE_DAYS`.
- 3.2.0 : `R.SCAN_DIR` et `R.TOLERANCE_DAYS` changent pendant que l'outil tourne (écran Réglages) : ne jamais les recopier dans une variable locale de module (`from .reglages import SCAN_DIR`), et ne pas les changer pendant une tâche (refusé : 409).
- 3.2.0 : changer le dossier analysé ne relance pas l'analyse ; l'ancienne liste reste affichée jusqu'à la suivante (le cache incrémental est invalidé, car ses paramètres incluent dossier et tolérance).
