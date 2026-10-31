# IA (facultative)

Moodle Bridge fonctionne très bien sans IA. Si tu en branches une, elle sert à **deux choses seulement** :

1. **Ranger les fichiers ambigus** des cours en mode `classify`. Moodle range souvent « TP du 1er octobre » ou « Supports 2026 » là où tu voudrais `TD/TD03` ou `CM/CM02`. Les règles sûres passent d'abord : fichier déjà présent, voisins déjà rangés, corrections. L'IA ne tranche que ce qui reste, et sa réponse est vérifiée : dossier autorisé, sous le cours, confiance suffisante. Sinon, le fichier va dans `_a_classer/`.
2. **Résumer les annonces longues** en quelques lignes, dans le tableau de bord.

## Aucune clé d'API

Moodle Bridge appelle une **ligne de commande** déjà installée et connectée sur ta machine. Tu restes dans ton abonnement habituel, sans clé à créer ni à stocker.

| `classify.engine` | Commande | Compte utilisé | Modèle par défaut |
|---|---|---|---|
| `claude-cli` | [Claude Code](https://docs.claude.com/claude-code) (`claude`) | abonnement Claude | `claude-haiku-5-5` |
| `codex-cli` | [Codex](https://github.com/openai/codex) (`codex`) | compte ChatGPT ou clé OpenAI déjà configurée | celui de Codex : indique un petit modèle disponible pour ton compte dans `classify.model` |
| `command` | n'importe quelle commande qui lit la demande sur son entrée standard et répond sur sa sortie | — | `classify.model`, remplacé dans `{model}` |
| `none` | — | — | — |

Exemples de réglages :

```json
"classify": {"engine": "claude-cli", "command": "/Users/moi/.local/bin/claude"}
"classify": {"engine": "codex-cli", "command": "C:\\Users\\moi\\AppData\\Roaming\\npm\\codex.cmd", "model": "<petit modèle>"}
"classify": {"engine": "command", "command": ["ollama", "run", "{model}"], "model": "llama3.2"}
```

Indique de préférence le **chemin complet** de la commande : `which claude` sous macOS et Linux, `where codex` sous Windows. La synchronisation automatique ne connaît pas toujours le `PATH` de ton terminal.

La commande doit fonctionner seule avant d'être branchée. Vérifie avec `claude -p "bonjour"`, `codex exec "bonjour"` ou `ollama run llama3.2 "bonjour"`. Si elle demande une connexion, fais `claude auth login` ou `codex login` toi-même.

## Résumés

Les résumés d'annonces (`summaries`) reprennent le moteur du rangement, sauf réglage contraire. Pour les couper : `"summaries": {"enabled": false}`.

## Confidentialité et coût

- **Envoyé à l'IA** :
  - noms des fichiers, des sections et des activités ;
  - arborescence du dossier du cours ;
  - quelques fichiers déjà rangés en exemple ;
  - tes règles de rangement ;
  - pour les résumés, le texte de l'annonce.
- **Jamais envoyé** : le contenu des documents, ton jeton, tes notes.
- Chaque appel se fait dans un **dossier temporaire vide**, sans outils quand la commande le permet. Claude Code est lancé sans outils, sans skills ni serveurs MCP ; Codex dans un bac à sable en lecture seule. L'IA ne peut donc ni lire ni modifier tes fichiers.
- **Coût** : un passage sans nouveauté n'appelle aucune IA. Chaque annonce n'est résumée qu'une fois. Compte un appel par lot de nouveaux fichiers ambigus d'un cours.
- Le texte des annonces est traité comme une **donnée** : la consigne interdit à l'IA d'obéir à une instruction qu'il contiendrait.

Pour une IA entièrement locale, sans aucun envoi : `command` avec [Ollama](https://ollama.com).
