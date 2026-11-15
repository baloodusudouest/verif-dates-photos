# Reprise — Éphéméride (vérification des dates)

Nom d'appli depuis la 2.0.1 : **Éphéméride**. Noms techniques inchangés (`verif-dates-photos`, `VERIF_…`).

Fichier à lire en premier dans une nouvelle session, et à mettre à jour à chaque fin de session, dans le même commit que le code.

## État (15/11/2026)

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

1. Mettre la 3.0.0 en service : Portainer › Recreate + Re-pull de `verif_dates_photos`, se reconnecter une fois (ou passer par l'Anneau). Conseillé pour le premier jour : `VERIF_LECTURE_SEULE=oui`, vérifier l'écran sur PC et iPhone, puis retirer la variable.
2. À essayer sur la vraie installation (jamais fait) : une correction réelle depuis la nouvelle interface, le renommage d'un dossier, la conversion d'un vrai `.MTS` de caméscope, la tuile de l'Anneau avec la sonde `/health`.
3. Retour arrière si besoin : étiquette `:2.2.0` dans la stack.

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
- Sans `VERIF_PASSWORD`, rien ne s'ouvre (3.0.0 : le conteneur démarre, mais toute adresse `/api/…` répond 503 ; avant la 3.0, il s'arrêtait).
