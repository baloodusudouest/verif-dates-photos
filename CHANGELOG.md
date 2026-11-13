# Journal des versions

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
