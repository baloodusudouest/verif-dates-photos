# verif-dates-photos

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
5. **Corrige uniquement les photos cochées**. La date est écrite dans le fichier, sans laisser de copie `_original`. Selon le type de fichier :
   - photos : EXIF et décalage horaire ;
   - vidéos : dates QuickTime ;
   - dans tous les cas : sidecar `.xmp` s'il existe, et date de modification du fichier.

   Ensuite, la page **relit** le fichier pour vérifier l'écriture, et inscrit chaque correction dans `/data/corrections.csv`.
6. **Range les photos corrigées** dans la vue « Déjà corrigées » (avant → après), hors de la liste à traiter. Si un nouveau scan trouve encore un écart sur une photo corrigée, elle revient dans « À traiter » avec la mention « Déjà corrigée le … ».
7. **Immich (facultatif)** : la page affiche la date connue d'Immich et ses miniatures. Après correction, elle lui demande de relire les métadonnées.

## Installation (Portainer)

```yaml
  verif-dates-photos:
    image: ghcr.io/baloodusudouest/verif-dates-photos:latest
    container_name: verif_dates_photos
    environment:
      - TZ=Europe/Paris
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
| `LOCAL_ROOT` | `/photos` | Dossier racine des photos dans le conteneur |
| `SCAN_SUBDIR` | `Pellicule` | Sous-dossier à analyser |
| `TOLERANCE_DAYS` | `1` | Écart toléré, en jours |
| `IMMICH_URL`, `IMMICH_API_KEY`, `IMMICH_ROOT` | — | Accès à Immich (facultatif) |
| `DATA_DIR` | `/data` | Résultats, cache, miniatures, journal des corrections, photos corrigées |
| `PORT` | `8099` | Port de la page |

### Permissions de la clé API Immich

- `asset.read` : lire la liste des fichiers et leurs dates ;
- `asset.view` : afficher les miniatures ;
- `job.create` : demander la relecture des métadonnées après correction.

Si la relecture est refusée, lance un scan de la bibliothèque externe.

## Sécurité

Les fichiers sont **modifiés sur place**. Fais une sauvegarde avant le premier passage, puis garde `corrections.csv` : il contient l'ancienne et la nouvelle date de chaque fichier.

La page n'a pas de mot de passe. Elle est à garder sur le réseau local ou Tailscale.

## Développement

```bash
pip install -r requirements-dev.txt   # exiftool doit aussi être installé
python -m pytest -q
```

L'image est construite par GitHub Actions et publiée sur GHCR avec les étiquettes `latest`, `<version>` et `sha-<commit>`.
