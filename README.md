# verif-dates-photos

> **Version 2.0.0** : même outil, mêmes règles, avec un mot de passe, un mode lecture seule et l'habillage commun aux autres applis (rail, tuiles, sections Connexion et Réglages). Voir `CHANGELOG.md`.

Petite page web qui vérifie les dates des photos d'une **Pellicule** rangée par dossiers
(`Pellicule/<année>/<AAAA-MM-JJ évènement>/`) avant de les migrer dans le stockage d'Immich.

## Ce qu'elle fait

1. **Analyse** chaque photo, vidéo et RAW avec exiftool. Elle compare la date de prise de vue :
   - à la **date du dossier** : `AAAA-MM-JJ`, sinon `AAAA-MM`, sinon `AAAA` ;
   - à la **date du nom du fichier**, si elle existe (`IMG_20190714_153000`, `2008-08-10-014136052`, `Screenshot_2019-07-14-15-20-00`…).
2. **Signale** :
   - les **écarts** de plus de `TOLERANCE_DAYS` jours avec le dossier, ou avec le nom du fichier quand le dossier n'est pas daté ;
   - les photos **sans date EXIF** ;
   - les photos **à trancher**, quand le nom du fichier contredit le dossier.
3. **Propose** une date pour chaque photo signalée, dans cet ordre de priorité :
   - la date du nom du fichier, avec son heure si elle y figure ;
   - sinon le **jour dominant des autres photos du dossier** (les « voisines » : photos du même dossier dont la date est cohérente), quand il regroupe au moins la moitié d'entre elles ;
   - sinon la date du dossier.

   L'heure d'origine est conservée, ou 12:00 si la photo n'a pas d'heure. Pour une photo à trancher, rien n'est proposé d'office : des boutons Dossier / Nom / Voisines te laissent choisir.
4. **Contrôle la cohérence** : chaque carte indique si la date choisie tombe dans la période des voisines (± tolérance). Une alerte ⚠ s'affiche sinon, un filtre regroupe ces photos, et une confirmation est demandée avant de les corriger.
   **Par dossier**, une barre d'actions applique d'un coup à toutes les photos du dossier l'un de ces choix : la date du nom, celle des voisines, celle du dossier, les dates proposées, ou un même jour en gardant l'heure de chaque photo. Chaque photo réglée est cochée. Le bouton « Corriger ce dossier » ne corrige ensuite que ce dossier.
   **Dossier mal daté**, quand les photos ont la bonne date mais pas le dossier (par exemple « 2007 - … » rempli de photos du 03/02/2008), deux solutions :
   - « Les dates des photos sont bonnes » : aucun fichier n'est modifié. Les photos ne sont plus signalées tant qu'elles ne changent pas (`/data/validees.json`), et elles apparaissent dans « Déjà corrigées » ;
   - « Renommer le dossier » : un nouveau nom est proposé d'après la date majoritaire des photos (`2008/2008-02-03 Bisca et dune du pila`), et il reste modifiable. Le dossier est déplacé sur le NAS, puis une analyse des fichiers déplacés est relancée. Immich verra ces photos comme de nouveaux fichiers.
5. **Corrige uniquement les photos cochées**. La date est écrite dans le fichier, sans laisser de copie `_original`. Selon le type de fichier :
   - photos : EXIF et décalage horaire ;
   - vidéos : dates QuickTime ;
   - dans tous les cas : sidecar `.xmp` s'il existe, et date de modification du fichier.

   - vidéos **AVI, MTS/M2TS et MKV** : exiftool ne sait pas écrire de date dans ces formats. La vidéo est donc **convertie en MP4** : les flux sont copiés sans perte, l'audio n'est réencodé en AAC que si nécessaire, et la vidéo en H.264 en dernier recours. Le MP4 est d'abord écrit dans un fichier temporaire, où la date est inscrite puis vérifiée, tout comme la durée. Le fichier est ensuite renommé, et l'original est **supprimé**, ou déplacé dans `ORIGINALS_DIR` si cette variable est définie. Si une étape échoue, l'original reste intact. Immich verra le MP4 comme un nouveau fichier.

   - fichiers que exiftool refuse d'écrire :
     - **contenu d'un autre format que l'extension** (par exemple un PNG nommé `.jpg`) : le fichier est renommé avec la bonne extension, puis daté ; Immich le verra comme un nouveau fichier. Si ce nom est déjà pris, deux cas se présentent : un fichier **identique octet pour octet** est un doublon, il est mis de côté dans `/data/doublons` et la date est écrite sur le fichier déjà bien nommé ; sinon, le fichier prend un nom alternatif (`nom-heic.jpg`) ;
     - **JPEG précédé d'octets parasites** : ces octets sont retirés sans toucher à l'image, et l'original est gardé dans `/data/originaux-nettoyes` ;
     - **fichier abîmé** (illisible ou tronqué) : il est signalé « abîmé » et n'est jamais modifié. Un filtre permet de les retrouver pour les remplacer depuis une sauvegarde.

   Ensuite, la page **relit** le fichier pour vérifier l'écriture, et inscrit chaque correction dans `/data/corrections.csv`.
6. **Range les photos corrigées** dans la vue « Déjà corrigées » (avant → après), hors de la liste à traiter. Si un nouveau scan trouve encore un écart sur une photo corrigée, elle revient dans « À traiter » avec la mention « Déjà corrigée le … ».
7. **Immich (facultatif)** : la page affiche la date connue d'Immich et ses miniatures. Après correction, elle lui demande de relire les métadonnées.

Pendant une analyse, la page affiche les trois étapes : lecture de l'index Immich, recensement des fichiers, analyse des dates. Pour chacune, elle indique :
- la durée, une fois l'étape terminée ;
- le temps restant, pour l'étape en cours ;
- une estimation, pour les étapes à venir, calculée à partir des mesures de l'analyse précédente (`/data/mesures.json`).

Elle affiche aussi le temps restant au total.

## Installation (Portainer)

```yaml
  verif-dates-photos:
    image: ghcr.io/baloodusudouest/verif-dates-photos:latest
    container_name: verif_dates_photos
    environment:
      - TZ=Europe/Paris
      - VERIF_PASSWORD=${CARNET_PASSWORD}             # obligatoire : le mot de passe commun aux applis
      - VERIF_LECTURE_SEULE=${VERIF_LECTURE_SEULE:-}  # « oui » : aucun fichier n'est modifié
      - LOCAL_ROOT=/photos
      - SCAN_SUBDIR=Pellicule
      - TOLERANCE_DAYS=1
      - IMMICH_URL=http://immich_server:2283
      - IMMICH_API_KEY=${VERIF_IMMICH_API_KEY}
      - IMMICH_ROOT=/synologyphotos   # le même dossier de photos, vu depuis le conteneur Immich
    ports:
      - "8099:8099"
    volumes:
      - /chemin/vers/Photos:/photos   # en écriture : nécessaire pour corriger
      - /chemin/vers/docker/verif-dates-photos:/data
    restart: unless-stopped
```

### Variables

| Variable | Défaut | Rôle |
|---|---|---|
| `VERIF_PASSWORD` | — (**obligatoire**) | Mot de passe de la page. Sans lui, l'outil ne démarre pas |
| `VERIF_LECTURE_SEULE` | — | `oui` : l'outil analyse et propose, mais ne modifie, ne déplace et ne supprime aucun fichier |
| `LOCAL_ROOT` | `/photos` | Dossier racine des photos dans le conteneur |
| `SCAN_SUBDIR` | `Pellicule` | Sous-dossier à analyser |
| `TOLERANCE_DAYS` | `1` | Écart toléré, en jours |
| `IMMICH_URL`, `IMMICH_API_KEY`, `IMMICH_ROOT` | — | Accès à Immich (facultatif) |
| `DATA_DIR` | `/data` | Résultats, cache, miniatures, journal des corrections, photos corrigées |
| `PORT` | `8099` | Port de la page |
| `ORIGINALS_DIR` | — | Si défini : dossier où déplacer les vidéos d'origine après conversion en MP4 (sinon elles sont supprimées). À placer hors de la bibliothèque Immich. |

### Permissions de la clé API Immich

- `asset.read` : lire la liste des fichiers et leurs dates ;
- `asset.view` : afficher les miniatures ;
- `job.create` : demander la relecture des métadonnées après correction.
- `asset.statistics` (facultatif) : connaître le nombre de photos dans Immich, pour estimer la durée de la lecture de l'index dès la première analyse.

Si la relecture est refusée, lance un scan de la bibliothèque externe.

## Sécurité

Les fichiers sont **modifiés sur place**. Fais une sauvegarde avant le premier passage, puis garde `corrections.csv` : il contient l'ancienne et la nouvelle date de chaque fichier.

Depuis la 1.11.0, la page demande un mot de passe (`VERIF_PASSWORD`) et rien ne répond sans session, sauf `/health`. Elle reste à garder sur le réseau local ou Tailscale.

Le mode lecture seule (`VERIF_LECTURE_SEULE=oui`) sert au premier jour d'une nouvelle version : l'analyse et les propositions fonctionnent, toute écriture est refusée, jusque dans les fonctions qui touchent aux fichiers.

Le retour à une version précédente ramène le code, pas les fichiers : une vidéo convertie dont l'original a été supprimé ne revient pas. Régler `ORIGINALS_DIR` pour garder les originaux.

## Développement

```bash
pip install -r requirements-dev.txt   # exiftool doit aussi être installé
python -m pytest -q
```

L'image est construite par GitHub Actions et publiée sur GHCR avec les étiquettes `latest`, `<version>` et `sha-<commit>`.
