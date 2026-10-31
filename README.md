# Moodle Bridge — le skill

Cette branche contient **uniquement le skill** de [Moodle Bridge](../../tree/main) : un mode d'emploi pour ton agent IA. Il fonctionne avec Claude Code, Codex, et tout agent qui lit les fichiers `SKILL.md`.

Moodle Bridge synchronise en lecture seule les documents, devoirs, échéances et annonces de ton Moodle dans un dossier de ton ordinateur, avec un tableau de bord Markdown à lire dans Obsidian ou VS Code. Le détail est dans le [README principal](../../tree/main#readme).

## Ce que fait ce skill

1. **La première fois**, il guide l'installation avec toi :
   - il te demande ton système, si tu veux une IA pour ranger les fichiers, l'application macOS et ton lecteur Markdown ;
   - il choisit l'édition qui te convient (`main` ou `cli-ia`) et clone le dépôt ;
   - il lance l'assistant d'installation.
2. **Une fois l'installation faite**, il se remplace par un skill relié à ton installation : il sait où sont tes cours, le tableau de bord, les réglages et les journaux, et quelles commandes lancer selon ta demande.

La **connexion à Moodle** reste toujours entre tes mains. L'agent te donne une commande à lancer dans ton propre terminal, et ton mot de passe comme ton jeton ne passent jamais par la conversation.

## Installer le skill

Choisis le dossier de skills de ton agent.

**Claude Code** (macOS, Linux) :

```bash
git clone --branch skill --single-branch https://github.com/ArthurArctique/Moodle-Bridge.git ~/.claude/skills/moodle-bridge
```

**Codex** (macOS, Linux) :

```bash
git clone --branch skill --single-branch https://github.com/ArthurArctique/Moodle-Bridge.git ~/.codex/skills/moodle-bridge
```

**Windows** (PowerShell), même chose avec le dossier de ton profil :

```powershell
git clone --branch skill --single-branch https://github.com/ArthurArctique/Moodle-Bridge.git $HOME\.claude\skills\moodle-bridge
```

Remplace `.claude` par `.codex` pour Codex.

**Sans git** : sur GitHub, choisis la branche `skill`, puis *Code → Download ZIP*. Décompresse l'archive dans un dossier `moodle-bridge` à l'intérieur du dossier de skills de ton agent.

**Autre agent** : place le dossier où ton agent cherche ses skills. S'il ne charge pas de skills, demande-lui de lire `SKILL.md`.

## L'utiliser

Ouvre une nouvelle session de ton agent et demande-lui, par exemple :

> Installe Moodle Bridge pour mon Moodle.

Après l'installation, tu peux lui demander :
- « quoi de neuf sur Moodle ? » ;
- « qu'est-ce que j'ai à rendre cette semaine ? » ;
- « range ce fichier » ;
- « comment est calculée la note de cette UE ? ».

## Contenu

| Fichier | Rôle |
|---|---|
| `SKILL.md` | le skill d'amorçage : questions, choix de l'édition, installation |
| `references/installation.md` | l'assistant, ses réponses et la première connexion |
| `references/fonctionnement.md` | fichiers, synchronisation, rangement, réglages, dépannage |
| `references/api-moodle.md`, `references/classement.md` | API Moodle utilisée, règles de rangement |
| `agents/openai.yaml` | présentation du skill dans Codex |

## Licence

[MIT](LICENSE).
