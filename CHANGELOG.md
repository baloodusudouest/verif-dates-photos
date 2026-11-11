# Journal des versions

## 1.11.0 — 11 novembre 2026
Sécurité, avant l'habillage commun. Les règles de dates et les écritures ne changent pas.
- **Mot de passe** (`VERIF_PASSWORD`, obligatoire) : la page et toutes ses adresses demandent une session ; seul `/health` répond sans. Sans la variable, l'outil ne démarre pas. Bouton « Se déconnecter ».
- **Mode lecture seule** (`VERIF_LECTURE_SEULE=oui`) : analyse et propositions, mais aucune correction, aucun renommage, aucune conversion. Le refus est fait deux fois : à l'adresse appelée, et dans chaque fonction qui touche à un fichier. Un test vérifie que l'empreinte et la date de chaque fichier ne bougent pas.
- Le contrôle de santé de l'image passe par `/health`.
- Tests : 19 (27 avec leurs variantes).

## 1.0 à 1.10.1 — novembre 2026
Voir l'en-tête de `app/app.py` et le README.
