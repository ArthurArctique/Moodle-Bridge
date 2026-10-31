# Fonctionnement du pont

Un script Python déterministe, sans dépendance hors de la bibliothèque standard, fait tout le travail fiable : interroger l'API, détecter ce qui est nouveau, télécharger, ranger sans jamais écraser, conserver les textes et écrire le coffre Obsidian. Un modèle de langage n'intervient, si on l'a configuré, que pour choisir le dossier d'un fichier ambigu et résumer une annonce. Un passage sans nouveauté ne consomme donc aucun token.

Le pont est **en lecture seule côté Moodle** : il ne rend aucun devoir, ne poste rien et ne marque rien comme lu.

## Fichiers d'une installation

`<env>` est le dossier des cours.

| Fichier | Rôle |
|---|---|
| `<env>/.moodle-bridge/config.json` | réglages (schéma plus bas) |
| `<env>/.moodle-bridge/state.json` | état : fichiers suivis et leur emplacement, textes, échéances, annonces, résumés |
| `<env>/.moodle-bridge/plan.json` | décisions d'une simulation (`sync --dry-run`), reprises au passage réel |
| `<env>/.moodle-bridge/logs/sync.log` | journal du pont |
| `<env>/Tableau de bord/` (`vault.dir`) | coffre Obsidian : notes générées, réécrites à chaque appel |
| `<env>/Moodle.md` (`index.path`, désactivé par défaut) | ancien tableau de bord en un fichier |
| `<env>/.moodle-bridge/textes/<CODE>.md` (`texts.dir`) | textes de chaque cours |
| cache du système (`~/Library/Caches/moodle-bridge`, `%LOCALAPPDATA%\moodle-bridge\Cache`, `~/.cache/moodle-bridge`) | téléchargements en cours |
| journaux des tâches planifiées (`~/Library/Logs/moodle-bridge`, `%LOCALAPPDATA%\moodle-bridge\logs`, `~/.local/share/moodle-bridge/logs`) | sortie de chaque passage planifié |

## Synchronisation

`sync` effectue un passage complet :
1. il télécharge dans le cache, hors de l'espace de travail ;
2. il range les fichiers ;
3. il met à jour l'état ;
4. il régénère le coffre Obsidian et les textes.

`--dry-run` fait tout sauf écrire dans l'espace de travail. Il enregistre ses décisions dans `plan.json`, que le passage réel suivant reprend tel quel : mêmes fichiers, aucun nouvel appel au modèle.

La tâche planifiée passe toutes les `schedule.interval_minutes`. Un passage manqué est rattrapé au réveil de l'ordinateur et à l'ouverture de session. Selon le système, elle s'appuie sur :
- macOS : launchd ;
- Windows : le Planificateur de tâches (dossier « MoodleBridge ») ;
- Linux : cron.

**Règle de rattrapage pour les agents** : au début d'une tâche qui touche aux cours, aux documents ou aux échéances, lancer `status --json`. Si `late` est vrai, lancer `sync` tout de suite et l'annoncer en une ligne. Ne pas le refaire dans la même conversation.

## Rangement

Le détail est dans `classement.md`. L'ordre des règles :
1. route ;
2. déjà dans Zotero ;
3. déjà présent (empreinte SHA-256) ;
4. correction (`overrides`) ;
5. miroir (mode `mirror`) ;
6. voisins (au moins deux fichiers de la même activité déjà rangés dans un même dossier) ;
7. modèle (dossier validé, confiance ≥ `min_confidence`) ;
8. boîte d'attente (`classify.inbox`, `_a_classer` par défaut).

Garanties :
- **jamais d'écrasement** : une version différente est rangée à côté, sous `nom (Moodle AAAA-MM-JJ).ext` ;
- aucun fichier annexe ni README ;
- vidéos et fichiers trop lourds seulement listés ;
- pages web Moodle jamais téléchargées comme fichiers ;
- un fichier déplacé à la main est retrouvé par son empreinte ;
- un fichier supprimé à la main n'est jamais retéléchargé.

**Classer un fichier en attente** : le déplacer dans le bon dossier, puis lancer `sync`. Pour que les versions futures suivent la même règle, ajouter une entrée `overrides`.

## Textes et modalités de contrôle

À chaque passage, le pont relève aussi les textes que l'API renvoie :
- résumés de sections ;
- étiquettes et descriptions d'activités ;
- contenu des pages ;
- consignes des devoirs ;
- texte des annonces.

Il les nettoie du HTML (Markdown lisible, liens sans jeton) et les conserve dans `state.json` (`remote.texts`), dans un fichier par cours (`texts.dir`) et, pour les modalités de contrôle, dans le coffre.

- **Dates** : une annonce garde sa date de publication et de modification. Une page ou un devoir garde la date de modification donnée par Moodle. Moodle ne date pas les résumés de sections ni les étiquettes : le pont note quand il les a relevés (`seen_at`) et quand il y a constaté un changement (`changed_at`). Le premier relevé d'un cours ne compte jamais comme une modification.
- **Modalités de contrôle (MCC)** : un texte est reconnu comme MCC si :
  - son titre ou sa section correspond à `texts.mcc_title` ;
  - ou son contenu correspond à `texts.mcc_content`.

  Il est recopié en entier dans la note « Modalités de contrôle » du coffre et dans celle de la matière, et sa modification déclenche une notification. Moodle fait foi : pour une question de coefficients, partir de ces textes et dater la réponse.
- **Coût** : nul, aucun modèle n'est appelé. `texts.enabled: false` désactive le relevé.

## Résumés des annonces

Si un modèle est configuré, chaque annonce longue est résumée en quelques lignes, une seule fois, et de nouveau seulement si son texte change. Le résumé s'affiche sous l'annonce dans le coffre. Le coffre n'appelle jamais le modèle : il montre seulement les résumés déjà faits. Une annonce courte est montrée telle quelle, sans modèle. Réglages : `summaries` (`enabled: false` pour s'en passer). Un moteur, une commande ou un modèle laissés vides reprennent ceux de `classify`.

## Coffre Obsidian

Le coffre (`vault.dir`, relatif à `<env>`) est **réécrit à chaque appel** : `sync`, réussi ou non, `status`, `courses`, `index`. Il est entièrement déterministe : aucun modèle n'intervient, et une note n'est réécrite que si son contenu change.

| Note | Contenu |
|---|---|
| `Accueil` | la semaine à venir, les dernières annonces, les nouveaux documents, ce qui attend une action, un tableau des matières |
| `Matières/<CODE>` | une note par matière : échéances, annonces, modalités de contrôle, notes, puis tout le contenu section par section |
| `Devoirs/<CODE> — <devoir>` | date limite, fermeture du dépôt, consignes, pièces jointes |
| `Échéances`, `Annonces`, `Documents`, `Modalités de contrôle` | les mêmes informations, tous cours confondus |

Une route dont le dossier sort de celui du cours (une matière hébergée dans le même cours Moodle) a sa propre note de matière.

**Notes personnelles** : le pont ne modifie et ne supprime que les notes qui portent la propriété `genere_par: moodle-bridge`. L'utilisateur peut écrire ses propres notes dans le coffre ; un agent ne modifie jamais une note générée à la main.

**Liens** : les notes se relient par des liens Obsidian. Les fichiers de cours situés dans le coffre (le dossier qui contient `.obsidian`, ou l'un de ses parents) ont des liens relatifs ; les autres, des liens `file://`.

`index.path` réactive l'ancien tableau de bord en un seul fichier, en plus du coffre ou à sa place (`vault.dir: null`).

## IA

| `classify.engine` | Commande | Modèle par défaut |
|---|---|---|
| `none` | — | — |
| `claude-cli` | `claude -p` (Claude Code, abonnement Claude) | `haiku` (alias : `haiku`, `sonnet`, `opus`) |
| `codex-cli` | `codex exec` (Codex, compte ChatGPT ou clé OpenAI) | celui de Codex ; `ai status --json` liste les modèles du compte |
| `command` | toute commande lisant l'entrée standard, par exemple `["ollama", "run", "{model}"]` | `model` |

`classify.command` donne le chemin de la commande. Mieux vaut un chemin absolu : la tâche planifiée n'a pas le `PATH` du terminal. Si la commande échoue, parce qu'elle est déconnectée ou à court de quota, les fichiers ambigus vont dans la boîte d'attente : rien n'est perdu. Le journal indique le modèle réellement utilisé.

**Changer d'IA ou de modèle** : la commande `ai` le fait sans éditer le JSON.
- `ai status [--json]` : moteur et modèle actuels, moteurs installés et connectés, modèles proposés ;
- `ai set --engine claude-cli|codex-cli|command|none [--model M] [--command C]` : écrit `classify.engine`, `classify.model` et le chemin absolu de la commande. Les réglages propres aux résumés (`summaries`) sont remis à vide pour suivre ce choix ;
- `ai login` : connexion de Claude Code ou de Codex, **dans le terminal de l'utilisateur** (elle passe par le navigateur) ;
- `ai test` : un court essai réel, avec le modèle effectivement utilisé ;
- `ai` seul : menu interactif pour l'utilisateur, aussi accessible par un double-clic sur `Choisir-IA.command` (macOS) ou `Choisir-IA.bat` (Windows).

Ce qui est envoyé au modèle :
- les noms des fichiers et des sections ;
- l'arborescence du cours ;
- des exemples de fichiers déjà rangés ;
- les règles ;
- le texte des annonces, pour les résumés.

Le contenu des documents n'est jamais envoyé.

## Supports de cours et Zotero

Avec `zotero.enabled`, le pont ne copie jamais un support de cours (diaporama de CM, polycopié, livre, article) dans l'arborescence : il le **met de côté** dans `zotero.staging` et le signale. L'import dans Zotero est une décision d'organisation (titre, collection, tags) qui revient à l'utilisateur. Un agent la prépare ainsi :
1. `zotero-pending --json` liste les fichiers en attente, avec des indices de tags ;
2. l'agent cherche un élément existant pour éviter un doublon ;
3. il présente le plan (titre, collection, tags) et attend l'accord de l'utilisateur ;
4. il importe avec l'outil Zotero de l'environnement.

Au passage suivant, `sync` retrouve le fichier dans `zotero.search_roots` par son empreinte et supprime la copie mise de côté.

## Jeton

Le jeton est rangé dans le coffre du système (voir `installation.md`). Ne jamais l'afficher, le copier dans une commande, un fichier ou un message, ni le lire autrement que par le script. Quand `sync` échoue avec « jeton refusé » (`invalidtoken`, `invalidtimedtoken`), l'utilisateur relance `token` **dans son terminal**. `token --forget` supprime le jeton.

## Réglages (`config.json`)

Toute clé absente prend la valeur par défaut de `scripts/mb/config.py`.

```json
{
  "version": 1,
  "site": "https://moodle.exemple.fr",
  "keychain_service": "moodle-bridge:moodle.exemple.fr",
  "env_root": "..",
  "default_mode": "mirror",
  "courses": {
    "1234": {"dest": "ABC", "code": "ABC", "mode": "classify", "name": "Nom du cours",
             "routes": [{"sections": "^TD\\d+\\s*-", "dest": "XYZ", "code": "XYZ"}]},
    "5678": {"dest": "Infos", "code": "INFO", "mode": "mirror"},
    "9999": {"skip": true}
  },
  "exclude": {"modnames": ["page", "book"], "list_only_mimetypes": ["video/", "audio/"], "max_size_mb": 200},
  "classify": {
    "engine": "claude-cli",
    "command": "/chemin/absolu/vers/claude",
    "model": null,
    "min_confidence": 0.8,
    "inbox": "_a_classer",
    "rules": "…",
    "context_files": [],
    "new_dir_patterns": ["(CM|TD|TP)/\\1\\d{2}", "Ressources", "Projet", "Examens"]
  },
  "summaries": {"enabled": null, "engine": null, "command": null, "model": null, "timeout_s": 120},
  "zotero": {"enabled": false, "token": "@zotero", "staging": ".moodle-bridge/a_importer_zotero", "search_roots": []},
  "overrides": {"1234/5555/fichier.pdf": "TD/TD03"},
  "vault": {"dir": "Tableau de bord", "title": "Moodle"},
  "index": {"path": null, "title": "Moodle", "news_days": 7, "announcements_per_course": 5},
  "texts": {"enabled": true, "dir": ".moodle-bridge/textes",
            "mcc_title": "modalit\\w*\\s+de\\s+contr[ôo]le|\\bMCCC?\\b",
            "mcc_content": "formule\\s+de\\s+calcul|calcul\\s+de\\s+la\\s+note"},
  "schedule": {"interval_minutes": 120},
  "notify": true
}
```

`env_root` est relatif au dossier de la configuration : `..` désigne le dossier des cours. Toutes les destinations sont relatives à `env_root`. Pour un Moodle dans une autre langue, adapter `texts.mcc_title` et `texts.mcc_content`.

## Dépannage

- `status` : dernier succès, retard, dernier échec, compteurs, état de la tâche planifiée. `status --check-token` vérifie aussi le jeton.
- **« jeton refusé »** : relancer `token`, par l'utilisateur.
- **« claude indisponible » / « Not logged in »** : la commande d'IA doit être connectée une fois par l'utilisateur, avec `claude auth login` ou `codex login`. En attendant, la boîte d'attente prend le relais.
- **Modèle Codex refusé** (« not supported ») : choisir un modèle proposé par `ai status --json`, puis `ai set`.
- **« Unable to find a vault »** dans Obsidian : le dossier qui contient `.obsidian` n'a pas encore été ouvert comme coffre (« Ouvrir un dossier comme coffre »).
- **macOS, `Operation not permitted`** dans le journal de la tâche : macOS refuse à Python l'accès au dossier, souvent dans iCloud, OneDrive ou Documents. L'utilisateur l'autorise dans *Réglages Système → Confidentialité et sécurité*, ou choisit un autre dossier.
- **Windows**, la tâche ne tourne pas : *Planificateur de tâches → MoodleBridge*, colonne « Résultat de la dernière exécution » ; ou `schedule status`.
- **Linux** : `crontab -l` doit contenir les lignes `# moodle-bridge:…`.
- Erreurs d'API et limites connues : `api-moodle.md`.
