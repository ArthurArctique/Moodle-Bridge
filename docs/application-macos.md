# Application de barre des menus (macOS, édition `main`)

Une petite icône dans la barre des menus. Un clic ouvre un panneau, et l'application n'a pas d'autre fenêtre.

## Ce qu'elle affiche

- **L'icône** :
  - à jour ;
  - nombre de nouveaux documents depuis la dernière ouverture du panneau ;
  - synchronisation en cours ;
  - en retard (aucun passage réussi depuis longtemps) ;
  - en échec : Moodle injoignable ou jeton expiré, la raison est dans le panneau.
- **Documents** : les derniers arrivés et le dossier où ils ont été rangés, un clic pour les ouvrir.
- **Échéances** : devoirs et événements à venir.
- **Annonces** : un clic affiche le résumé (ou le texte d'une annonce courte), un autre ouvre l'annonce sur Moodle.
- **Notes** (si activées) : une nouvelle note arrive en **mystère**, avec un emoji, une couleur et une énigme. Sa valeur ne s'affiche qu'au clic sur « Découvrir ».
- **IA** : l'IA utilisée et son modèle, à changer d'un clic. Voir [Changer de modèle](ia.md#changer-de-modèle).
- **Historique** des derniers passages et bouton **Synchroniser**, qui lance tout de suite la tâche planifiée.
- **Coffre** : ouvre la note d'accueil du coffre Obsidian. Si Obsidian ne connaît pas encore ce coffre, la note s'ouvre dans l'éditeur par défaut et le panneau indique quel dossier ouvrir comme coffre.

L'application **ne contacte jamais Moodle** et n'a pas accès au jeton. Seul l'onglet IA lance une commande : celle du pont (`ai status`, `ai set`, `ai test`), qui interroge Claude Code ou Codex sur ta machine. Elle lit l'état que Moodle Bridge écrit dans `~/Library/Caches/moodle-bridge/` à chaque passage. Une seule application sert toutes les installations du Mac.

## Installation

L'assistant la propose à la fin de l'installation. Sinon :

```bash
python3 scripts/moodle_bridge.py menubar install --config ~/Moodle/.moodle-bridge/config.json
```

- Elle est **compilée sur ta machine**, en une dizaine de secondes. Il faut macOS 13 ou plus récent et les outils de développement d'Apple : `xcode-select --install`, une fois.
- Elle s'installe dans `~/Applications/Moodle Bridge.app` et se lance.
- **Ouverture à la connexion** : menu ⋯ du panneau, « Ouvrir à la connexion ». macOS peut demander de confirmer dans *Réglages Système → Général → Ouverture*.
- Après une mise à jour du dépôt (`git pull`), relance `menubar install` pour recompiler.
- `menubar uninstall` la supprime.

## Je ne vois pas l'icône

Si `pgrep -x MoodleBridge` affiche un numéro, l'application tourne, mais son icône est cachée par l'une de ces causes :
- **manque de place** : macOS masque d'abord les icônes les plus éloignées de l'horloge quand l'application au premier plan a beaucoup de menus. Essaie depuis le Finder ;
- une application **en plein écran** : la barre des menus n'apparaît qu'au survol du haut de l'écran ;
- un **gestionnaire de barre des menus** (Bartender, Ice…) : fais passer l'icône dans la partie visible.

Sinon, relance-la : `open ~/Applications/Moodle\ Bridge.app`.
