# Installer Moodle Bridge

Pour l'agent qui accompagne une installation. Les étapes sont détaillées pour l'utilisateur dans le `README.md` et dans `docs/premiere-connexion.md` du dépôt.

## L'assistant

`install.py`, à la racine du dépôt, équivaut à `scripts/moodle_bridge.py init`. Il enchaîne les étapes suivantes :

1. **Site** : l'utilisateur colle l'adresse de son Moodle, n'importe quelle page convient. L'assistant vérifie que l'API de l'appli mobile est ouverte et repère le type de connexion.
2. **Dossier des cours** : `~/Moodle` par défaut, ou un sous-dossier d'un coffre Obsidian existant.
3. **Coffre Obsidian** : `Tableau de bord/` dans le dossier des cours par défaut. Si Obsidian est installé et que le dossier des cours n'est dans aucun coffre, l'assistant y crée `.obsidian` pour qu'Obsidian l'ouvre comme coffre. Les textes vont dans `.moodle-bridge/textes/`.
4. **IA** (éditions `main` et `cli-ia`) : aucune, Claude Code, Codex ou une autre commande. L'assistant ne propose que les commandes présentes sur la machine.
5. **Fréquence** : 120 min par défaut, 15 min au minimum.
6. **Connexion à Moodle** : voir plus bas. Le jeton est vérifié auprès de Moodle.
7. **Cours** : tous les cours où l'utilisateur est inscrit, chacun dans un dossier tiré de son nom court. L'utilisateur peut en exclure.
8. **Première synchronisation**, **tâche planifiée**, puis selon l'édition l'**application** macOS et le **skill**.

Relancer l'assistant sur un dossier déjà installé met l'installation à jour : les cours et réglages existants sont conservés, les nouveaux cours ajoutés.

## Mode non interactif (`init --answers`)

Utile quand c'est l'agent qui pose les questions. Les réponses sont passées en JSON, dans un fichier ou sur l'entrée standard avec `-`. Toute clé absente prend sa valeur par défaut.

| Clé | Valeur | Défaut |
|---|---|---|
| `site` | adresse du Moodle (obligatoire) | — |
| `env` | dossier des cours | `~/Moodle` |
| `reuse` | mettre à jour une installation existante dans `env` | `true` |
| `vault` | dossier des notes Obsidian générées, relatif à `env` (`null` ou `"aucun"` : pas de coffre) | `Tableau de bord` |
| `index` | ancien tableau de bord en un fichier, relatif à `env` | `null` |
| `texts_dir` | dossier des textes, relatif à `env` | `.moodle-bridge/textes` |
| `engine` | `none`, `claude-cli`, `codex-cli`, `command` | la première commande trouvée, sinon `none` |
| `command` | chemin de `claude` ou `codex`, ou commande complète pour `command` (liste ou chaîne ; `{model}` y est remplacé) | trouvé dans le `PATH` |
| `model` | modèle de l'IA | petit modèle par défaut du moteur |
| `mode` | `classify` (dossiers CM/TD/TP, avec IA) ou `mirror` (sections Moodle) | `classify` avec IA, sinon `mirror` |
| `interval` | minutes entre deux synchronisations | `120` |
| `exclude_courses` | identifiants Moodle des cours à ne pas suivre | `[]` |
| `first_sync` | lancer la première synchronisation | `true` |
| `schedule` | installer la tâche planifiée (**accord de l'utilisateur requis**) | `true` |
| `menubar` | installer l'application macOS (**accord requis**) | `true` |
| `skill` | `claude`, `codex`, `both` ou `none` | `none` |

Exemple :

```json
{"site": "https://moodle.exemple.fr", "env": "~/Moodle", "engine": "codex-cli", "model": "<modèle>",
 "schedule": true, "menubar": false, "skill": "codex"}
```

La sortie est un JSON qui contient `config`, `env`, `steps` (étapes faites) et `pending`. Tant que le jeton manque, `pending` vaut `["token"]` et `next` donne la commande à transmettre à l'utilisateur. Une fois qu'il s'est connecté, relancer la même commande.

## Première connexion à Moodle

Le pont utilise le service web de l'appli mobile officielle : il voit ce que voit l'étudiant dans l'appli, rien de plus. Moodle lui délivre un **jeton**, rangé dans le coffre du système :
- Trousseau sous macOS ;
- Gestionnaire d'identification sous Windows ;
- `secret-tool`, ou à défaut un fichier lisible par l'utilisateur seul, sous Linux.

La variable d'environnement `MOODLE_BRIDGE_TOKEN`, si elle existe, est prioritaire.

`token --config <…>/config.json` choisit la méthode d'après le type de connexion du site. `--method` l'impose.

| Méthode | Pour | Ce que fait l'utilisateur |
|---|---|---|
| `password` | comptes Moodle classiques : identifiant et mot de passe saisis sur la page de Moodle | saisit son identifiant et son mot de passe dans le terminal ; le mot de passe sert une fois à obtenir le jeton (`login/token.php`) et n'est jamais enregistré |
| `browser` | authentification unique (CAS, Shibboleth, ENT, compte Microsoft…) | connecté à Moodle dans son navigateur, ouvre les outils de développement (onglet Réseau, journal conservé), ouvre dans cet onglet le lien affiché, copie l'adresse `moodlemobile://token=…` qui apparaît dans le journal et la colle dans le terminal (ou Entrée seule : lecture du presse-papiers) ; la signature est vérifiée, le presse-papiers vidé |
| `key` | sites qui affichent la clé de l'appli mobile | copie, sur la page *Préférences → Clés de sécurité* (`/user/managetoken.php`), la clé du service « Moodle mobile web service » (pas la clé RSS) et la colle dans le terminal |

C'est toujours **l'utilisateur** qui lance `token`, dans son propre terminal. L'agent ne demande ni mot de passe ni jeton dans la conversation, ne lit pas le coffre et ne copie jamais un jeton dans une commande.

Si le site refuse l'appli mobile (`enablewebservices` ou `enablemobilewebservice` désactivés), le pont ne peut pas fonctionner. Seul l'établissement peut l'activer.

## Installer dans un espace de travail existant

L'assistant convient à un dossier neuf. Pour brancher le pont sur une arborescence déjà organisée, comme un dossier de cours par matière ou un coffre Obsidian, lancer l'assistant avec ce dossier comme `env`, puis ajuster les réglages avec l'utilisateur :

- **courses** : pour chaque cours, `dest` (dossier existant, relatif à `env`), `code`, `mode` :
  - `classify` : rangement par séance (CM/TD/TP), par voisins, puis par l'IA ;
  - `mirror` : un dossier par section Moodle, pour un cours administratif ;
  - `search_root` (facultatif) : dossier plus large où repérer les fichiers déjà présents ;
  - `skip: true` : ignorer un cours ;
  - `routes` : envoyer certaines sections d'un cours vers une autre destination, par exemple `{"sections": "^TD\\d+\\s*-", "dest": "Cours/XYZ", "code": "XYZ"}`.
- **classify.rules** : les conventions de nommage en quelques lignes. Exemple : « CM/CM01, TD/TD01, numéro sur deux chiffres ; la numérotation locale fait foi ».
- **classify.context_files** : fichiers de contexte donnés à l'IA, comme un index de correspondances ou un README d'arborescence. Pour un gros fichier, seules les lignes qui citent le code du cours sont envoyées.
- **zotero** : si les supports de cours (diaporamas de CM, polycopiés, articles) vont dans une bibliothèque Zotero plutôt que dans les dossiers, régler `enabled: true`, `search_roots` (dossiers des pièces jointes) et ajouter la règle `dest = "@zotero"` dans `classify.rules`. L'import lui-même reste fait par un agent, avec l'accord de l'utilisateur (`references/fonctionnement.md`).
- **vault.dir**, **texts.dir** : le coffre peut aussi être placé hors du dossier des cours (chemin absolu), par exemple dans un coffre Obsidian existant ; les liens vers les fichiers deviennent alors des liens `file://`.
- **Instructions de l'environnement** (`AGENTS.md`, `CLAUDE.md` ou équivalent), après accord sur le texte :
  - le chemin de la configuration ;
  - la règle de rattrapage : `status` en début de tâche, `sync` si en retard ;
  - les écritures automatiques autorisées : copies dans les destinations, boîte d'attente, notes générées du coffre, textes ;
  - l'interdiction d'écrire vers Moodle et d'afficher le jeton.

Pour un premier passage sur une arborescence existante, lancer d'abord `sync --dry-run`. Présenter ensuite le plan, ajouter les corrections dans `overrides`, puis lancer `sync`.

## Application de barre des menus (macOS, édition `main`)

- **Prérequis** : macOS 13 ou plus récent, et `swiftc`. S'il manque, l'utilisateur lance lui-même `xcode-select --install`.
- **Installation** : `menubar install --config …` compile l'application sur place, l'installe dans `~/Applications/Moodle Bridge.app` et la lance. Une seule application sert tous les environnements du Mac.
- **Ouverture à la connexion** : c'est l'utilisateur qui l'active, dans le menu ⋯ du panneau.

## Skill des agents

`skill install --config … --target claude|codex|both`, ou `--dest <dossier>` pour un autre agent, écrit un `SKILL.md` adapté à l'installation : chemins, commandes et parties présentes. Il remplace le skill d'amorçage s'il est présent. Il refuse un dossier qui contient autre chose qu'un skill moodle-bridge généré. Relancer la commande après un changement d'installation.
