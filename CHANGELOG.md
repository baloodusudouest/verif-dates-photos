# Journal des versions

## 3.0.0 — 15 novembre 2026
**Format commun du socle : FastAPI pour le serveur, Svelte 5 et Tailwind 4 pour l'interface.** Deuxième des deux livraisons choisies par Julien. Les règles de dates, les écritures dans les fichiers et les fichiers d'état sont ceux de la 2.2.0 : les modules `dates`, `exif`, `analyse` et `etat` n'ont pas changé, et ce que faisaient les adresses Flask (lancer une analyse, corriger, réaligner, valider ou renommer un dossier) est passé tel quel dans `actions.py`.
- **Serveur FastAPI** (`app/main.py`) lancé par Uvicorn ; Flask et `app/web.py` sont retirés. Adresses : `/api/etat`, `/api/progression`, `/api/analyse`, `/api/corriger`, `/api/realigner`, `/api/valider`, `/api/renommer`, `/api/miniature/…`, `/api/journal.csv`. Publiques : `/health`, les icônes, `/api/session`, `/api/connexion`.
- **Connexion** par la classe `Sessions` du socle (cookie `verif_dates_session`, trente jours ; une seconde d'attente, cinq après cinq essais faux en cinq minutes) ; le cookie commun `maison_session` de l'Anneau ouvre toujours l'appli. « Se déconnecter » ferme toutes les sessions ouvertes avant. **Il faudra se reconnecter une fois** (ou passer par l'Anneau) : l'ancien cookie Flask ne vaut plus.
- **Interface Svelte** : mêmes sections (À traiter, Corrigées, Connexion, Réglages), mêmes tuiles, mêmes gestes et mêmes confirmations qu'en 2.x (date du nom, voisines, date du dossier, dates proposées, même jour pour toutes, cocher, corriger ce dossier, « les dates des photos sont bonnes », renommer, réaligner), dossier replié puis passage au suivant, filtre gardé d'une visite à l'autre. Les anciennes adresses de section (`#corrigees`…) restent valables.
- **`/health`** donne aussi le nombre de photos à traiter et si une tâche tourne, sans aucun nom de fichier ni de dossier, pour la tuile de l'Anneau.
- **Correction** : un `.mts` en flux MPEG-TS simple était pris pour un fichier abîmé (« le contenu n'est pas une photo ni une vidéo (M2T) ») et n'avait donc pas de case « Corriger ». Il est maintenant reconnu comme vidéo à convertir en MP4. Trouvé en réécrivant le test de conversion.
- **Sur iPhone**, un long mot (le chemin du journal dans le message de fin de correction) ne peut plus élargir la page.
- Image : une étape Node compile l'interface ; l'outil tourne toujours en root (il garde le propriétaire des photos).
- Tests : les 28 tests de l'outil passent, leurs attentes sur la page HTML étant réécrites en attentes sur `/api/etat` ; 6 tests d'écran sur PC et iPhone (dont corriger un dossier puis relire la date dans le fichier, et choisir une date à trancher, filtrer, valider un dossier). Sur GitHub, l'image démarre et sert l'interface avant d'être publiée.
- Retour arrière : étiquette `:2.2.0` à la place de `:latest` dans la stack (mêmes fichiers d'état).

## 2.2.0 — 15 novembre 2026
- **Le fichier unique est découpé en modules**, sans changer une règle ni une écriture : `reglages` (variables et chemins), `etat` (état, verrous, fichiers d'état, journal), `dates` (règles de dates, aucune écriture), `exif` (lecture et écriture dans les fichiers, réparations), `immich`, `analyse`, `actions` (corrections, réalignement) et `web` (page et adresses, toujours Flask). Le découpage a été fait par un script : chaque fonction est recopiée telle quelle, seules les références d'un module à l'autre sont réécrites. Les 34 tests passent sans qu'une seule attente ait changé. Première des deux livraisons vers FastAPI et Svelte.

## 2.1.2 — 15 novembre 2026
- **Verrou d'écriture** (recommandé par le conseil). Les fichiers d'état (`results.json`, `cache.json`, `corrigees.json`, `validees.json`, `mesures.json`, `videos-realignees.json`) et le journal `corrections.csv` ne sont plus écrits que par une action à la fois, chaque fichier d'état passant par un fichier temporaire remplacé d'un coup. Avant, deux actions simultanées (par exemple « Les dates des photos sont bonnes » pendant une correction) pouvaient faire échouer l'enregistrement ou laisser un fichier à moitié écrit. Rien ne change dans les règles de dates ni dans les écritures des photos.

## 2.1.1 — 13 novembre 2026
- **Toujours sombre.** Le thème clair est retiré : l'appli est sombre partout, ouverte seule comme dans l'Anneau, quel que soit le réglage du téléphone.

## 2.1.0 — 13 novembre 2026
- **Connexion commune.** Connecté à l'Anneau, on n'a plus à se connecter ici : l'appli accepte le cookie « maison_session » que l'Anneau pose, quand elle est servie par la même adresse que lui. Le cookie est signé avec le mot de passe commun : une appli qui a un autre mot de passe continue de demander le sien. Il vaut trente jours, ou jusqu'au changement du mot de passe ; « Se déconnecter » le retire du navigateur.

## 2.0.2 — 12 novembre 2026
- Icône de l'appli (onglet du navigateur, écran d'accueil de l'iPhone) : la page d'un éphéméride. Le nom « Éphéméride » est aussi celui de l'icône sur l'iPhone.

## 2.0.1 — 12 novembre 2026
- L'appli s'appelle maintenant **Éphéméride** (titre de la page, écran du mot de passe, rail). Rien d'autre ne change : le dépôt, l'image, le conteneur et les variables gardent leur nom technique.

## 2.0.0 — 11 novembre 2026
L'outil prend l'habillage et la navigation communs aux applis, par la voie médiane retenue après le conseil : **le serveur, les adresses qui écrivent, les règles de dates et les fichiers d'état ne changent pas**. Seuls le style de la page et son organisation changent.
- **Habillage commun** : fonds neutres chauds, sombre par défaut et clair quand l'appareil le demande, couleur **bleu ardoise**.
- **Navigation** : rail à gauche sur PC, pilule d'icônes en bas sur téléphone — À traiter, Corrigées, Connexion, Réglages.
- **Tuiles à la place du menu de filtre** : Tout, Écarts de date, Sans date, À trancher, Hors période des voisines, Extension incorrecte, Fichiers abîmés, chacune avec son nombre. Une tuile filtre la liste, comme le faisait le menu.
- **Connexion** : état de la Pellicule (dossier, dernière analyse, lecture seule) et d'Immich. **Réglages** : tolérance, sort des vidéos converties, journal `corrections.csv`, déconnexion.
- Sur téléphone, l'en-tête ne reste plus collé en haut (il masquerait les photos).
- Tests : 19 de l'outil (27 avec leurs variantes), inchangés, et 2 tests d'écran sur PC et sur téléphone, dont « corriger un dossier puis relire la date dans le fichier ». Les tests d'écran tournent sur le poste de développement, pas encore sur GitHub.

## 1.11.0 — 11 novembre 2026
Sécurité, avant l'habillage commun. Les règles de dates et les écritures ne changent pas.
- **Mot de passe** (`VERIF_PASSWORD`, obligatoire) : la page et toutes ses adresses demandent une session ; seul `/health` répond sans. Sans la variable, l'outil ne démarre pas. Bouton « Se déconnecter ».
- **Mode lecture seule** (`VERIF_LECTURE_SEULE=oui`) : analyse et propositions, mais aucune correction, aucun renommage, aucune conversion. Le refus est fait deux fois : à l'adresse appelée, et dans chaque fonction qui touche à un fichier. Un test vérifie que l'empreinte et la date de chaque fichier ne bougent pas.
- Le contrôle de santé de l'image passe par `/health`.
- Tests : 19 (27 avec leurs variantes).

## 1.0 à 1.10.1 — novembre 2026
Voir l'en-tête de `app/app.py` et le README.
