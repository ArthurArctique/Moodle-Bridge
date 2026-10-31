# Dépannage

Dans les commandes ci-dessous, remplace `~/Moodle` par ton dossier de cours. Sous Windows, utilise `py` à la place de `python3`.

## Le premier réflexe : `status`

```bash
python3 scripts/moodle_bridge.py status --config ~/Moodle/.moodle-bridge/config.json --check-token
```

Il indique :
- la dernière synchronisation réussie et le **dernier échec** avec sa raison ;
- le nombre de fichiers par état et ceux à classer ;
- l'état de la **tâche planifiée** ;
- la validité du **jeton**.

Journaux :

| Journal | Où |
|---|---|
| journal du pont | `~/Moodle/.moodle-bridge/logs/sync.log` |
| sortie de la tâche planifiée | macOS : `~/Library/Logs/moodle-bridge/` ; Windows : `%LOCALAPPDATA%\moodle-bridge\logs\` ; Linux : `~/.local/share/moodle-bridge/logs/` |

## Problèmes fréquents

| Symptôme | Cause et solution |
|---|---|
| « jeton refusé » (`invalidtoken`, `invalidtimedtoken`) | Le jeton a expiré ou a été révoqué. Relance `token` : voir [premiere-connexion.md](premiere-connexion.md). |
| « identifiant ou mot de passe refusé » alors qu'ils sont justes | Ton établissement passe par une page de connexion unique : `token --method browser`. |
| « l'API de l'appli mobile est désactivée » | Ton Moodle refuse l'appli mobile. Seul l'établissement peut l'activer. |
| « Moodle inaccessible : HTTP 503 » ou maintenance | Moodle est indisponible. Le passage suivant réessaiera tout seul. |
| `CERTIFICATE_VERIFY_FAILED` (macOS) | Le Python de python.org n'a pas encore ses certificats : lance *Applications → Python 3.x → Install Certificates.command*. |
| Des fichiers dans `_a_classer/` | Normal : le pont préfère ne pas deviner. Déplace-les dans le bon dossier, la synchronisation suivante les retrouve par leur empreinte. |
| Un nouveau cours « non configuré » | `courses --add-all`, ou relance `install.py`. |
| Les vidéos ne sont pas téléchargées | Volontaire : elles sont listées avec leur lien Moodle (`exclude.list_only_mimetypes`). |

## La synchronisation automatique ne tourne pas

### macOS

`schedule status` doit indiquer `loaded: true`.

Si le journal de la tâche contient `Operation not permitted`, macOS refuse à Python, lancé en arrière-plan, l'accès au dossier des cours. C'est fréquent dans *Documents*, *Bureau*, iCloud Drive ou OneDrive. Deux solutions :
- donne l'accès dans *Réglages Système → Confidentialité et sécurité → Accès complet au disque* à l'interpréteur Python indiqué par `schedule status` ;
- ou choisis un dossier de cours hors de ces emplacements, comme `~/Moodle`.

### Windows

Ouvre le *Planificateur de tâches*, puis le dossier **MoodleBridge**. La colonne « Résultat de la dernière exécution » doit valoir `0x0`.
- La tâche tourne avec `pythonw.exe`, sans fenêtre, sous ton compte, à l'ouverture de session puis à intervalle régulier.
- Si Python a été réinstallé ailleurs, relance `schedule install`.

### Linux

`crontab -l` doit contenir deux lignes terminées par `# moodle-bridge:…`. Le service cron doit être actif : `systemctl status cron`, ou `cronie` selon la distribution.

## Repartir de zéro sans perdre ses fichiers

1. `uninstall --config …` retire la tâche et le jeton.
2. Supprime le dossier `.moodle-bridge/` du dossier des cours.
3. Relance `install.py` avec le même dossier.

Les fichiers déjà présents sont reconnus par leur empreinte et ne sont pas retéléchargés.
