# Classement des fichiers Moodle

Les sections Moodle correspondent rarement une à une à l'arborescence locale : un enseignant peut intituler « TP du 1er octobre » une séance que l'étudiant range en `TD/TD03`, ou regrouper tous ses CM dans une seule section. Le pont classe donc **fichier par fichier**, en partant des indices les plus sûrs.

## Ordre des règles (`scripts/mb/sync.py`, `scripts/mb/classify.py`)

| Ordre | Règle | Quand elle s'applique | Résultat |
|---|---|---|---|
| 0 | Route | section correspondant à une route du cours | la suite s'applique à la destination de la route |
| 1 | Version inchangée | fichier modifié sur Moodle mais contenu identique | métadonnées mises à jour |
| 2 | Déjà dans Zotero | empreinte identique à un fichier sous `zotero.search_roots` | rien n'est copié |
| 2 bis | Déjà présent | empreinte identique à un fichier sous `search_root` | rien n'est copié, emplacement mémorisé ; avec Zotero, classement quand même : si c'est un support de cours (`@zotero`, par le modèle ou une correction), une copie est mise de côté pour l'import et la copie locale est signalée |
| 3 | Correction | clé présente dans `overrides` (dossier ou `@zotero`) | destination imposée |
| 4 | Miroir | cours en mode `mirror` | `<dest>/<section>/` |
| 5 | Plan relu | décision de la dernière simulation, même empreinte | décision reprise |
| 6 | Version précédente | nouvelle version d'un fichier déjà rangé | même dossier, nom suffixé |
| 7 | Voisins | au moins deux fichiers déjà rangés de la même activité, tous dans le même dossier | ce dossier |
| 8 | Modèle | `classify.engine` différent de `none` | dossier validé si confiance ≥ `min_confidence` |
| 9 | Boîte d'attente | aucun des cas ci-dessus | `<dest>/<inbox>/` |

La règle des voisins est rejouée tant qu'elle produit de nouveaux placements : un fichier rangé sert d'exemple aux suivants. Elle ne s'appuie jamais sur la section seule ni sur un exemple unique : une section « Interventions de X » ou « CM 2026-2027 » regroupe plusieurs séances, et généraliser un seul exemple rangerait CM2 avec CM1. Ces cas vont au modèle, qui reçoit les mêmes exemples et peut en tirer la correspondance section → dossier quand elle existe.

## Ce que reçoit le modèle

Un appel par cours (par lots de 40 fichiers) avec :

- les règles de rangement (`classify.rules`) et les extraits des fichiers de contexte ;
- l'arborescence locale du cours (dossiers et fichiers, sans les dossiers cachés ni la boîte d'attente) ;
- des exemples de fichiers déjà rangés : `section | activité | fichier → dossier` ;
- la liste des fichiers à ranger : section, activité, type d'activité, nom, date.

Il doit répondre uniquement par `{"placements": [{"id", "dest", "confidence", "reason"}]}`. La consigne lui demande de réutiliser un dossier existant quand le fichier appartient à une séance déjà rangée, et de baisser sa confiance au moindre doute sur le type ou le numéro.

L'appel passe par la ligne de commande choisie (`scripts/mb/llm.py`), toujours lancée dans un dossier temporaire vide :

- `claude-cli` : `claude -p --model <modèle> --tools "" --setting-sources "" --strict-mcp-config --output-format json --no-session-persistence` ; aucun outil, skill, serveur MCP ni réglage de projet ;
- `codex-cli` : `codex exec --sandbox read-only --ephemeral --skip-git-repo-check -o <fichier> [-m <modèle>] -` ; bac à sable en lecture seule, session éphémère ;
- `command` : la commande configurée reçoit la demande sur son entrée standard et répond sur sa sortie standard (par exemple `ollama run {model}`).

Quand `zotero.enabled` est vrai, la consigne ajoute que les supports de cours (diaporamas ou polycopiés de CM, chapitres et résumés de cours, livres, articles) ne se rangent pas dans l'arborescence : le modèle répond alors `dest = "@zotero"`. Le fichier est mis de côté dans `zotero.staging/<code>/` en attendant un import confirmé par l'utilisateur (voir `fonctionnement.md`, « Supports de cours et Zotero »).

## Validation

Une destination du modèle est acceptée seulement si :

- c'est le jeton Zotero (`@zotero`) quand Zotero est activé ; sinon :
- elle est relative, sans `..`, sans composant caché, et reste sous la racine du cours ;
- ce n'est pas la boîte d'attente ;
- c'est un dossier existant, ou elle correspond entièrement à l'un des `new_dir_patterns` (par défaut `CM/CM07`, `TD/TD02`, `TP/TP11`, `Ressources`, `Projet`) ;
- la confiance atteint `min_confidence` (0,8 par défaut).

Sinon le fichier va dans la boîte d'attente, avec la proposition refusée et sa raison, visibles dans le coffre (accueil et note de la matière).

## Corriger un placement

- **Avant le passage réel** (après `sync --dry-run`) : ajouter la clé du fichier dans `overrides`. La clé est `<courseid>/<cmid><filepath><nom>`, telle qu'elle figure dans `plan.json` ou `state.json`. Exemple : `"4242/123456/sujet.pdf": "TD/TD07"`.
- **Après coup** : déplacer le fichier à la main. Au passage suivant, le pont le retrouve par son empreinte et met à jour son emplacement. Une boîte d'attente vidée de cette façon fait passer le fichier en « rangé ».
- Pour qu'une **future version** suive la même destination sans rien faire, la règle « version précédente » suffit ; `overrides` reste utile pour un fichier qui n'a jamais été rangé.

## Collisions de noms

- Même nom, même contenu → considéré comme déjà présent.
- Même nom, contenu différent → nouvelle copie `nom (Moodle AAAA-MM-JJ).ext` (date de modification Moodle), puis `… 2`, `… 3` si nécessaire. L'original n'est jamais modifié : il peut contenir des annotations de l'utilisateur.
- La copie passe par un fichier caché `.nom.moodle-part` renommé à la fin : un passage interrompu ne laisse jamais de fichier tronqué sous son nom définitif.
