# Journal des versions

## 3.3.0 — 18 novembre 2026 (santé des tâches de fond)
- **`/health` dit l'état des tâches** (module commun `maison.py`, socle 0.9.1) ; les anciens champs (`ok`, `version`, `a_traiter`, `en_cours`) ne changent pas. Nouveaux champs : `version_socle`, `taches`, `copie_base`.
- Tâches suivies, toutes à la demande (l'outil n'a aucune boucle régulière) : **analyse** (« Tout analyser », « Scanner les nouveaux », et l'analyse qui suit un renommage de dossier), **corrections** (écriture des dates) et **realignement** (vidéos). Une tâche qui plante passe en « erreur » ; elle ne met jamais la tuile de l'Anneau en orange.
- Les messages d'erreur des tâches ne sortent qu'avec le jeton commun (`MAISON_JETON`) ou une session ouverte ; jamais de nom de fichier sans eux.
- Copie des bases : aucune (pas de base SQLite) ; `copie_base` vaut `null`.
- Comportement inchangé : une tâche qui plante montre toujours son erreur à l'écran comme avant.

## 3.2.5 — 18 novembre 2026 (constats moyens de l'audit)
- **M46** : chaque appel à exiftool a un délai maximal selon ce qu'il fait (lire un fichier : 120 s ; lire un lot de 100 fichiers : 900 s ; écrire une date : 900 s) ; ffmpeg et ffprobe avaient déjà le leur (3.2.1). Une écriture ou une relecture trop longue est un échec clair pour ce fichier, les autres continuent ; un lot trop long est relu fichier par fichier et l'analyse s'arrête en nommant le fichier qui bloque. Le réalignement des vidéos a le même délai.
- **M47** : avant la première écriture de sa date, chaque photo est copiée telle quelle dans `originaux-avant-date/` sous le dossier de données de l'appli (`/data`, déjà monté : rien à changer dans la stack), une seule fois par fichier. Si la copie échoue (disque plein…), la date n'est pas écrite. Pas pour les vidéos (une vidéo convertie garde sa règle : `ORIGINALS_DIR`). Ce dossier grossit avec chaque photo corrigée : à vider à la main quand les corrections sont vérifiées.
- **M48** : `save_corrected` écrivait déjà sous le verrou de sa photographie (3.2.1) ; `save_validated` et `save_results` le font maintenant aussi (même défaut : une photographie ancienne pouvait écraser une plus récente).
- `maison.py` du socle 0.8.2 (audit M3) : réglages secrets reconnus par mots, adresse avec identifiant écartée, champ `ecartes` dans la sauvegarde (noms seulement).

## 3.2.4 — 18 novembre 2026 (cookies Secure)
- **Cookies `Secure` en https** (audit du 17/11/2026, même correctif dans toutes les applis maison) : ouverte en https (directement, derrière Tailscale Serve qui le dit par `X-Forwarded-Proto`, ou d'après l'origine de la page), l'appli pose ses cookies de session avec l'attribut `Secure` : ils ne partent jamais en clair. En http (adresse locale), rien ne change.

## 3.2.3 — 18 novembre 2026 (révocation du cookie commun)
- **« Se déconnecter » dans l'Anneau ferme vraiment la connexion commune** (audit du 17/11/2026, même correctif dans toutes les applis maison) : le cookie commun `maison_session` porte maintenant la génération tenue par l'Anneau (« génération.expiration.signature ») ; « Se déconnecter » l'augmente, et chaque appli la lit dans l'annuaire de l'Anneau (`/externe/services`, au plus une minute de retard) : un cookie commun d'avant la déconnexion ne vaut plus nulle part. Si l'Anneau ne répond pas, le cookie reste accepté comme avant (rien ne se bloque). **Toutes les applis doivent être à jour ensemble** ; une seule reconnexion est demandée après la mise à jour.
- Module commun `maison.py` (socle 0.8.0) : `generation_commune()`, `definir_generation_commune()` ; l'annuaire lit aussi `generation_commune` (une ancienne réponse de l'Anneau, liste seule, reste lue).

## 3.2.2 — 17 novembre 2026 (connexion plus sûre)
- **Connexion plus sûre** (audit du 17/11/2026, même correctif dans toutes les applis maison) :
  - un cookie portant un caractère non ASCII donnait une erreur 500 sur toute l'API ; il est maintenant simplement refusé ;
  - les clés qui signent les cookies (celui de l'appli et le cookie commun `maison_session` posé par l'Anneau) sont tirées du mot de passe par PBKDF2 : un cookie capté sur le réseau local ne permet plus de chercher le mot de passe à grande vitesse. **Toutes les applis doivent être à jour ensemble** ; une seule reconnexion est demandée après la mise à jour ;
  - des essais de mot de passe lancés en parallèle ne contournent plus le délai : au-delà de 30 essais en 5 minutes, tout est refusé sans comparer.
- Module commun `maison.py` (socle 0.7.0) : l'annuaire de l'Anneau est relu 30 s après un échec (avant : 10 min sans adresses après un redémarrage de la stack) ; un réglage `nan` ou infini est refusé ; un `reglages.json` abîmé est mis de côté (`.corrompu`) au lieu d'être écrasé.

## 3.2.1 — 17 novembre 2026 (corrections de l'audit)
- **Sécurité des originaux** : à la conversion d'une vidéo (AVI, MTS, MKV) en MP4, l'original était supprimé même si la durée de la source n'avait pas pu être lue (le contrôle était alors sauté), et la tolérance de 1 % laissait passer 36 s manquantes sur une heure. Maintenant, sans durée lisible des deux côtés, la conversion est refusée et l'original reste ; tolérance resserrée à 1 s ou 0,2 %. Conseil : poser `ORIGINALS_DIR` dans la stack pour garder chaque original converti.
- ffmpeg et ffprobe ont un délai maximal : un fichier abîmé ne laisse plus la tâche « en cours » pour toujours.
- Le `.xmp` d'un original converti n'écrase plus un `.xmp` déjà présent à côté du MP4.
- **Bug** : l'état des fichiers corrigés pouvait perdre une ligne quand plusieurs écritures se croisaient (photographie et écriture sous deux verrous différents) ; c'était la cause de l'échec intermittent de `test_ecritures_simultanees`.
- Tests : un de plus (durée illisible).

## 3.2.0 — 16 novembre 2026
- **Les réglages passent dans l'appli** : le dossier analysé (`sous_dossier`, « Pellicule » par défaut) et la tolérance (`tolerance_jours`, 1 jour par défaut) se changent dans l'écran **Réglages**, sans redémarrer. Ils sont gardés dans `/data/reglages.json`. Au premier démarrage, ils sont repris une fois des anciennes variables `SCAN_SUBDIR` et `TOLERANCE_DAYS` si elles sont encore dans la stack (une ligne au journal `corrections.csv` pour chacun) ; ensuite ces variables ne sont plus lues. Chaque changement est noté au journal. Pas de changement pendant une analyse ou une correction.
- **Sauvegarde pour l'Anneau** : `GET /externe/sauvegarde` rend ces réglages et la version (contrat de sauvegarde 1). La porte ne s'ouvre qu'au jeton commun `MAISON_JETON` ; sans lui, elle reste fermée. Jamais de variable, de chemin ni de secret dans la réponse ; les renseignements fixés par la stack (lecture seule, originaux gardés) n'en sortent jamais.
- **Restaurer les réglages** (écran Réglages) : choisir une date de sauvegarde, voir les différences (avant → sauvegardé), confirmer. Message clair si l'Anneau ne répond pas ou n'a rien. Permis même en lecture seule : seuls les réglages de l'outil changent, aucune photo.
- **Anciens noms retirés** : le mot de passe ne se lit plus que dans `MAISON_PASSWORD` (12 caractères au moins) ; `VERIF_PASSWORD` seule ne suffit plus et l'outil refuse de démarrer. `VERIF_LECTURE_SEULE` reste (forçage propre à cet outil).
- Module commun `app/maison.py` : contrat 2 du socle, copié tel quel.
- Tests : reprise des anciennes variables au premier démarrage puis plus jamais, changement par l'écran, valeurs refusées (400), porte de sauvegarde (jeton commun seulement, ni secret ni clé exclue, contrat 1), restauration (dates, différences, application, Anneau injoignable, lecture seule) ; test d'écran Réglages et Restaurer sur PC et iPhone, sans débordement. Les écritures dans les photos n'ont pas changé : les preuves de lecture seule passent telles quelles.

## 3.1.0 — 16 novembre 2026
- **Variables communes de la maison** (module `app/maison.py` du socle 0.5.0, copié tel quel) :
  - mot de passe : `MAISON_PASSWORD`, repli sur `VERIF_PASSWORD` ; sans l'un ni l'autre, rien ne s'ouvre, comme avant ;
  - lecture seule : `MAISON_LECTURE_SEULE=oui` met aussi cet outil en lecture seule ; `VERIF_LECTURE_SEULE`, si elle dit oui ou non, l'emporte (« 1 », « true », « yes » restent acceptés). Réglages, Connexion et le bandeau d'À traiter disent si la lecture seule vient de toute la maison ou de cette appli.
- Pas de jeton entre applis maison ni de lien vers une autre appli dans cet outil : rien d'autre ne change.
- Rien ne change tant que la stack n'a pas les nouvelles variables : `VERIF_PASSWORD` et `VERIF_LECTURE_SEULE` restent lues.
- Tests : copie du module non retouchée, mot de passe commun puis repli, lecture seule de la maison et forçage par l'appli, et preuve qu'avec `MAISON_LECTURE_SEULE=oui` aucun octet ni aucune date de fichier ne bouge (adresses et fonctions d'écriture). 42 tests de l'outil et 6 tests d'écran passent.

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
