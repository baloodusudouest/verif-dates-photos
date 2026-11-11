# Reprise — vérification des dates

Fichier à lire en premier dans une nouvelle session, et à mettre à jour à chaque fin de session, dans le même commit que le code.

## État (11/11/2026)

- **2.0.0 sur `main`** : habillage commun en bleu ardoise, rail, tuiles à la place du filtre, sections Connexion et Réglages. Serveur, adresses, règles et fichiers d'état inchangés.
- 1.11.0 : mot de passe obligatoire, mode lecture seule.
- Dossier « tel que construit » et écarts avec carnet : [`docs/dossier.md`](docs/dossier.md). Avis du conseil : [`docs/2.0-conseil.md`](docs/2.0-conseil.md).
- Décisions de Julien (11/11) : l'outil **continuera de servir après la migration** ; **voie médiane** (habillage et navigation communs sur le serveur actuel, sans toucher aux adresses qui écrivent) ; l'original d'une vidéo convertie reste supprimé par défaut (`ORIGINALS_DIR` pour le garder) ; couleur bleu ardoise.

## Prochaine étape

1. Mise en service par Julien : `VERIF_PASSWORD` dans la stack (déjà dans `homelab.yml` du projet), un premier passage en `VERIF_LECTURE_SEULE=oui`. Retour arrière : `VERIF_DATES_VERSION=1.11.0`, ou `1.10.1` (sans mot de passe).
2. Avant de migrer la Pellicule : sauvegarde vérifiée, puis un dossier témoin importé dans Immich pour contrôler que les dates corrigées y sont bien lues (fuseau des vidéos, MP4 convertis).
3. Plus tard, puisque l'outil doit vivre : découpage du fichier unique en modules, puis FastAPI et Svelte, en deux livraisons séparées, après un essai sur une copie de quelques dossiers.

## Décisions tranchées

- L'outil modifie les fichiers de la photothèque : Watchtower désactivé, mise à jour manuelle.
- Les règles de dates et les fonctions d'écriture ne se réécrivent pas à l'occasion d'un changement d'interface.
- Pas de bouton « remettre l'ancienne date » : `corrections.csv` et la sauvegarde suffisent (avis du conseil).
- Rien de personnel dans le dépôt public.

## Pièges

- 2.0 : la page reste un modèle dans `app/app.py`. Le script s'appuie sur des identifiants et des classes (`#filtre`, `#f`, `.card`, `[data-folder]`, `.actions`, `#corrigees`) : le menu `#filtre` existe toujours, caché, et les tuiles le pilotent. Ne pas les renommer.
- Les tests d'écran (`tests/interface/`) créent de vrais fichiers et ont besoin d'exiftool et de Playwright ; ils ne tournent pas dans l'image sur GitHub (ils y sont sautés).
- Les tests ont besoin d'exiftool et de ffmpeg ; sans exiftool, la plupart sont sautés sans bruit. Sur GitHub ils tournent dans l'image.
- Le client de test ouvre la session tout seul (`tests/test_dates.py`, fixture `app`) : un test qui veut un visiteur sans session crée un `FlaskClient` nu.
- Revenir à une ancienne image ne rend pas les fichiers modifiés.
- Sans `VERIF_PASSWORD`, le conteneur s'arrête au démarrage : poser la variable dans la stack **avant** de passer à la 1.11.0.
