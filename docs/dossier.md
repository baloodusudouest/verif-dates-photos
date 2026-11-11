# Dossier de la vérification des dates — tel que construit (version 2.0.0)

Rédigé le 11/11/2026 à partir du code, à la réouverture de l'outil pour le mettre au même format que carnet (2.10) et Millésime (2.0). Six sections, puis les écarts et le plan proposé. **Mis à jour le même jour pour la 2.0.0** (voie médiane retenue après le conseil) : les sections décrivent la 1.10.1, suivies de ce qui a changé.

---

## 1. Besoin

Avant de migrer la bibliothèque externe (la Pellicule, rangée en `Pellicule/<année>/<AAAA-MM-JJ évènement>/`) vers le stockage d'Immich, s'assurer que chaque photo et chaque vidéo porte la bonne date de prise de vue, et corriger celles qui ne l'ont pas. Une fois migrées, les photos seront rangées par Immich d'après cette date.

| Besoin | Réponse actuelle |
|---|---|
| Repérer les dates fausses | Comparaison de la date de prise de vue avec la date du dossier, et avec la date du nom du fichier quand il en a une |
| Proposer une date | Dans l'ordre : date du nom, jour dominant des autres photos du dossier (« voisines »), date du dossier ; l'heure d'origine est gardée |
| Ne rien écrire sans accord | Seules les photos cochées sont corrigées ; confirmation quand la date sort de la période des voisines |
| Traiter un dossier d'un coup | Barre d'actions par dossier ; « Les dates des photos sont bonnes » ; « Renommer le dossier » |
| Garder une trace | `corrections.csv` (ancienne et nouvelle date), vue « Déjà corrigées », relecture du fichier après écriture |
| Cas tordus | Vidéos AVI, MTS, MKV converties en MP4 ; extension fausse ; JPEG précédé d'octets parasites ; fichier abîmé jamais modifié ; vidéos à réaligner |

C'est un **outil de chantier** : il sert jusqu'à la migration, puis ne sert plus. Utilisateur unique, sur PC surtout.

## 2. Technique

- **Un seul fichier** `app/app.py` (1 810 lignes) : Flask, la logique des dates, les écritures et la page HTML (modèle Jinja, styles et script dans le même fichier). Identifiants de code en anglais, textes en français.
- **Outils** : exiftool (lecture et écriture des dates), ffmpeg (conversion en MP4), Pillow (miniatures).
- **État** : fichiers JSON dans `/data` (résultats, cache par fichier, corrigées, validées, mesures de durée) ; `corrections.csv`. Pas de base de données.
- **Immich** (facultatif) : clé API ; lecture de l'index et des miniatures, demande de relecture des métadonnées après correction.
- **Écriture sur place** dans les fichiers de la photothèque (volume monté en écriture).
- **Pas de mot de passe.** Port 8099, réseau local et Tailscale.
- **Publication** : GitHub Actions construit l'image, lance les tests dedans, publie seulement s'ils passent. Watchtower désactivé.
- **Tests** : 17 tests, sur de vrais fichiers créés pour l'occasion (exiftool et ffmpeg réels). Aucun test d'écran.

## 3. Parcours des clics et des écrans

Une seule page.

| Zone | Gestes |
|---|---|
| En-tête | Scanner les nouveaux ; Tout rescanner (après confirmation) ; filtre (tout, écarts, sans date, à trancher, incohérent avec les voisines, abîmés, extension incorrecte, déjà corrigées) ; Tout cocher, Tout décocher ; **Corriger la sélection** ; ligne de bilan |
| Progression | Trois étapes (index Immich, recensement, analyse) avec durée, temps restant et estimation |
| Par dossier | Replier ; appliquer à tout le dossier la date du nom, des voisines, du dossier, les dates proposées, ou un même jour ; Cocher le dossier ; **Corriger ce dossier** ; « Les dates des photos sont bonnes » ; Renommer le dossier |
| Par photo | Miniature, dates connues (fichier, Immich, dossier, nom), date proposée modifiable, case à cocher ; pour une photo à trancher : boutons Dossier, Nom, Voisines ; alerte si la date sort de la période des voisines |
| Déjà corrigées | Avant → après, par dossier |
| Vidéos à réaligner | Bandeau et bouton, quand une ancienne version en a touché à tort |

Adresses : `/` (page), `/scan`, `/apply`, `/progress`, `/valider`, `/renommer`, `/realigner`, `/thumb/<id>`, `/journal.csv`.

### Depuis la 2.0.0

Même page et mêmes gestes, réorganisés : rail (À traiter, Corrigées, Connexion, Réglages), tuiles à la place du menu de filtre, « Déjà corrigées » devenue l'entrée Corrigées, deux sections en lecture (Connexion, Réglages). Un mot de passe est demandé à l'entrée (1.11.0). Adresses ajoutées : `/connexion`, `/deconnexion`, `/health`.

## 4. Design

**Depuis la 2.0.0** : habillage commun aux applis (fonds neutres chauds, sombre par défaut et clair selon l'appareil, tuiles), couleur bleu ardoise. Ce qui suit décrit la 1.10.1.

Page utilitaire : styles écrits à la main dans le modèle, sombre, dense, pensée pour le PC. Pas de navigation.

## 5. Schéma de données

| Fichier de `/data` | Contenu |
|---|---|
| `results.json` | Dernière analyse : une entrée par fichier signalé, et le bilan |
| `cache.json` | Résultat par fichier (taille, date de modification), pour « Scanner les nouveaux » |
| `corrigees.json` | Photos corrigées : avant, après, date de la correction |
| `validees.json` | Photos déclarées bonnes telles quelles, avec leur signature |
| `mesures.json` | Durées de la dernière analyse, pour les estimations |
| `corrections.csv` | Journal de chaque écriture |
| `thumbs/`, `originaux-nettoyes/`, `doublons/` | Miniatures, originaux gardés, doublons mis de côté |

## 6. Plan d'implémentation

Fait : versions 1.0 à 1.10.1 (novembre 2026). Proposé : voir plus bas.

---

## Écarts avec carnet et Millésime

| # | Écart | Constat | Gravité | Proposition |
|---|---|---|---|---|
| 1 | Mot de passe | Aucun, sur un outil qui modifie les fichiers de la photothèque | **Fort** | Mot de passe commun et session par cookie, comme carnet et Millésime |
| 2 | Lecture seule | Pas de mode lecture seule | Fort | Variable de lecture seule : analyse et propositions, aucune écriture de fichier |
| 3 | Technique d'interface | Page HTML dans le fichier Python | Voulu à l'origine, à aligner (décision du 11/11) | FastAPI pour l'API, Svelte 5 et Tailwind 4 pour l'interface |
| 4 | Organisation du code | Un fichier de 1 810 lignes mêle règles de dates, écriture des fichiers, Immich et page | Moyen | Modules séparés ; **les règles de dates et les écritures ne sont pas réécrites**, seulement déplacées, sous la garde des 17 tests |
| 5 | Habillage | Styles propres, sombre seulement | Moyen | Habillage commun, couleur **bleu ardoise** (décision du 11/11) |
| 6 | Navigation | Une page, un filtre déroulant | Moyen | Quatre entrées : **À traiter** (une tuile par motif : écarts, sans date, à trancher, incohérentes, extension incorrecte, abîmées), **Corrigées** (avant → après, journal), **Connexion** (Immich, la Pellicule, sauvegardes), **Réglages** |
| 7 | Écran de travail | Tout sur une page, dossiers empilés | Moyen | Une tuile ouvre la liste de ses dossiers ; un dossier ouvre ses photos avec la même barre d'actions qu'aujourd'hui. Aucun geste de correction n'est retiré |
| 8 | État | Fichiers JSON réécrits en entier | Faible | Gardés tels quels : même format, pour qu'un retour à la 1.10 reste possible |
| 9 | Annulation | `corrections.csv` garde l'ancienne date, mais rien ne la remet | Moyen | Bouton « Remettre l'ancienne date » dans Corrigées, pour les corrections simples (pas les conversions ni les renommages) |
| 10 | Tests d'écran | Aucun | Moyen | Parcours « ouvrir un dossier, corriger, vérifier le fichier » sur PC et iPhone |
| 11 | Documents | Pas de `REPRISE.md` ni de journal des versions | Faible | Les ajouter |
| 12 | Données personnelles | Un prénom dans un nom de dossier d'exemple des tests | Faible | Le remplacer ; l'historique ne contient rien d'autre (ni adresse, ni chemin, ni nom complet) |
| 13 | iPhone | Page pensée pour le PC | Faible | L'habillage commun la rend utilisable ; le travail par dossier reste plus confortable sur PC |

**Déjà conforme, à garder :** tests sur de vrais fichiers, image publiée seulement si les tests passent, Watchtower désactivé, relecture du fichier après écriture, journal des corrections, fichiers abîmés jamais modifiés.

## Plan proposé

| Version | Contenu | Vérification |
|---|---|---|
| **1.11.0** | Mot de passe commun (1), lecture seule (2), prénom retiré des tests (12), documents (11). Toujours Flask, même page | Les 17 tests passent ; sans mot de passe, la page ne répond pas ; en lecture seule, « Corriger » est refusé |
| **2.0.0** | Découpage en modules sans changer une règle (4), FastAPI et Svelte (3), habillage bleu ardoise (5), quatre entrées et tuiles (6, 7), tests d'écran (10) | Les 17 tests passent sans modification de leurs attentes ; mêmes fichiers d'état ; un jour en lecture seule ; retour par `VERIF_DATES_VERSION=1.11.0` |
| **2.1.0** | Remettre l'ancienne date (9) | Test : corriger puis remettre rend le fichier identique, date pour date |

**Tranché par Julien le 11/11/2026, après le conseil** : l'outil continuera de servir après la migration ; voie médiane (habillage et navigation sur le serveur actuel) ; pas de corbeille par défaut ; pas de « remettre l'ancienne date ». Faits : écarts 1 et 2 (1.11.0), 5, 6 et 10 (2.0.0, sans changer de serveur), 11 et 12. Restent pour plus tard, puisque l'outil doit vivre : 3 et 4 (modules, puis FastAPI et Svelte), en livraisons séparées.

Questions posées avant :
1. Réécrire l'interface (2.0.0) alors que l'outil sert jusqu'à la migration seulement, ou s'arrêter à la 1.11.0 et rhabiller la page existante.
2. Quatre entrées avec tuiles, ou garder une seule page de travail avec un filtre.
3. « Remettre l'ancienne date » : maintenant, plus tard, ou jamais.
