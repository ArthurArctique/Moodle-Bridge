# IA (facultative)

Moodle Bridge fonctionne très bien sans IA : la synchronisation et le coffre Obsidian n'en ont jamais besoin. Si tu en branches une, elle sert à **trois choses seulement** :

1. **Ranger les fichiers ambigus** des cours en mode `classify`. Moodle range souvent « TP du 1er octobre » ou « Supports 2026 » là où tu voudrais `TD/TD03` ou `CM/CM02`. Les règles sûres passent d'abord : fichier déjà présent, voisins déjà rangés, corrections. L'IA ne tranche que ce qui reste, et sa réponse est vérifiée : dossier autorisé, sous le cours, confiance suffisante. Sinon, le fichier va dans `_a_classer/`.
2. **Résumer les annonces longues** en quelques lignes, dans le coffre Obsidian et dans l'application macOS.
3. **Habiller les notes mystère** (édition `main`) : un emoji, une couleur et une énigme.

## Changer de modèle

Pas besoin d'éditer de fichier. Trois façons, au choix :

### Dans l'application macOS

Menu **Moodle Bridge** → onglet **IA**.
- La liste montre Claude Code, Codex, Gemini, une autre commande et « Aucune IA », avec leur état : *connecté*, *non connecté* ou *non installé*.
- **Clique sur une IA** pour l'utiliser. Si elle n'est pas connectée, un terminal s'ouvre pour la connexion : suis les indications, souvent une page de connexion dans le navigateur.
- **Choisis le modèle** dans la liste en dessous. Pour Claude Code : Haiku (rapide et économique, recommandé), Sonnet ou Opus. Pour Codex et Gemini : les modèles de ton compte, avec pour Gemini le plus léger des Flash en tête (recommandé).
- **Tester** fait un appel minimal et affiche le temps de réponse et le modèle réellement servi.
- **Dans le terminal…** ouvre le choix pas à pas, utile pour « Autre commande » (Ollama…).
- Le bouton ↻ revérifie l'état, par exemple après une connexion.

### Avec le lanceur, sous macOS ou Windows

Double-clique sur **`Choisir-IA.command`** (macOS) ou **`Choisir-IA.bat`** (Windows), à la racine du dossier de Moodle Bridge. Une fenêtre de terminal :
1. montre le réglage actuel et les IA installées, connectées ou non ;
2. propose de se connecter si besoin ;
3. liste les modèles disponibles ;
4. enregistre ton choix et propose un essai.

Sous macOS, si le Finder refuse d'ouvrir le fichier la première fois : clic droit → *Ouvrir*.

### Dans un terminal

```bash
python3 scripts/moodle_bridge.py ai                                     # questions pas à pas
python3 scripts/moodle_bridge.py ai status                              # IA utilisée, installées, connectées
python3 scripts/moodle_bridge.py ai set --engine claude-cli --model sonnet
python3 scripts/moodle_bridge.py ai set --engine codex-cli              # modèle par défaut de Codex
python3 scripts/moodle_bridge.py ai set --engine gemini-cli --model <modèle Flash>
python3 scripts/moodle_bridge.py ai set --engine none                   # plus d'IA
python3 scripts/moodle_bridge.py ai login --engine codex-cli            # se connecter
python3 scripts/moodle_bridge.py ai test                                # essai
```

Sous Windows, remplace `python3` par `py`. `--config <dossier des cours>/.moodle-bridge/config.json` n'est nécessaire que si tu as plusieurs installations.

Le choix vaut pour tout : rangement, résumés et énigmes. Un réglage particulier des résumés (`summaries`) ou des énigmes (`notes.mystery`) est retiré à cette occasion, pour qu'ils suivent ton choix.

### Installer Claude Code, Codex ou Gemini

- **Claude Code** : voir https://docs.claude.com/claude-code. Sous macOS et Linux : `curl -fsSL https://claude.ai/install.sh | bash`. Connexion : `claude auth login`.
- **Codex** : voir https://github.com/openai/codex, par exemple `npm install -g @openai/codex`. Connexion : `codex login`.
- **Gemini** : pour les comptes Google gratuits et Google One, Gemini passe depuis juin 2026 par **Antigravity CLI**, la commande `agy` : voir https://antigravity.google. Connexion : ouvre `agy` une fois dans un terminal et connecte-toi avec ton compte Google. Avec une offre payante ou une clé d'API, Gemini CLI (`npm install -g @google/gemini-cli`) convient aussi : Moodle Bridge la prend quand `agy` est absent. Seul le passage par Antigravity a été essayé.

Puis reviens au choix de l'IA : la nouvelle commande y apparaît.

## Aucune clé d'API

Moodle Bridge appelle une **ligne de commande** déjà installée et connectée sur ta machine. Tu restes dans ton abonnement habituel, sans clé à créer ni à stocker.

| `classify.engine` | Commande | Compte utilisé | Modèle par défaut |
|---|---|---|---|
| `claude-cli` | [Claude Code](https://docs.claude.com/claude-code) (`claude`) | abonnement Claude | `haiku` (le dernier Haiku) |
| `codex-cli` | [Codex](https://github.com/openai/codex) (`codex`) | compte ChatGPT ou clé OpenAI déjà configurée | celui de Codex : indique un petit modèle disponible pour ton compte dans `classify.model` |
| `gemini-cli` | [Antigravity CLI](https://antigravity.google) (`agy`), sinon [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini`) | compte Google ; offre payante ou clé d'API pour Gemini CLI | celui de la commande : l'onglet IA et `ai` proposent d'abord le plus léger des Flash |
| `command` | n'importe quelle commande qui lit la demande sur son entrée standard et répond sur sa sortie | — | `classify.model`, remplacé dans `{model}` |
| `none` | — | — | — |

Le plus simple est de passer par [Changer de modèle](#changer-de-modèle). Exemples de réglages à la main :

```json
"classify": {"engine": "claude-cli", "command": "/Users/moi/.local/bin/claude"}
"classify": {"engine": "codex-cli", "command": "C:\\Users\\moi\\AppData\\Roaming\\npm\\codex.cmd", "model": "<petit modèle>"}
"classify": {"engine": "gemini-cli", "command": "/Users/moi/.local/bin/agy", "model": "<modèle Flash de agy models>"}
"classify": {"engine": "command", "command": ["ollama", "run", "{model}"], "model": "llama3.2"}
```

Indique de préférence le **chemin complet** de la commande : `which claude` sous macOS et Linux, `where codex` sous Windows. La synchronisation automatique ne connaît pas toujours le `PATH` de ton terminal.

La commande doit fonctionner seule avant d'être branchée. Vérifie avec `claude -p "bonjour"`, `codex exec "bonjour"`, `agy -p "bonjour"` ou `ollama run llama3.2 "bonjour"`. Si elle demande une connexion, fais `claude auth login` ou `codex login` toi-même, ou ouvre `agy` une fois.

## Résumés et notes

Les résumés d'annonces (`summaries`) et les énigmes des notes mystère (`notes.mystery`, édition `main`) reprennent le moteur du rangement, sauf réglage contraire. Pour les couper : `"summaries": {"enabled": false}`.

## Confidentialité et coût

- **Envoyé à l'IA** :
  - noms des fichiers, des sections et des activités ;
  - arborescence du dossier du cours ;
  - quelques fichiers déjà rangés en exemple ;
  - tes règles de rangement ;
  - pour les résumés, le texte de l'annonce.
- **Jamais envoyé** : le contenu des documents, ton jeton, tes notes (sauf la note elle-même au modèle de l'énigme mystère, si les notes sont activées).
- Chaque appel se fait dans un **dossier temporaire vide**, sans outils quand la commande le permet. Claude Code est lancé sans outils, sans skills ni serveurs MCP ; Codex dans un bac à sable en lecture seule ; Antigravity dans son bac à sable, sans terminal pour accepter une écriture ou une commande. L'IA ne peut donc ni lire ni modifier tes fichiers. Avec Antigravity, chaque appel reste dans son historique de conversations.
- **Coût** : un passage sans nouveauté n'appelle aucune IA. Chaque annonce n'est résumée qu'une fois. Compte un appel par lot de nouveaux fichiers ambigus d'un cours.
- Le texte des annonces est traité comme une **donnée** : la consigne interdit à l'IA d'obéir à une instruction qu'il contiendrait.

Pour une IA entièrement locale, sans aucun envoi : `command` avec [Ollama](https://ollama.com).
