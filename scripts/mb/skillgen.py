"""Skill pour agents IA (Claude Code, Codex…) adapté à une installation : chemins, commandes, parties présentes.

Le modèle `skill/templates/SKILL.md` contient des repères `{{NOM}}` et des blocs conditionnels :

    <!-- if ai -->…<!-- endif -->      gardé si la condition est vraie
    <!-- if !ai -->…<!-- endif -->     gardé si elle est fausse

Le skill installé remplace le skill d'amorçage (`skill/SKILL.md`, qui guide la première installation) ;
les deux se reconnaissent à leur marqueur `<!-- moodle-bridge-skill: … -->`.
"""

import re
import shutil
import sys
from pathlib import Path

from . import FEATURES, EDITION, __version__, schedule, system

REPO = Path(__file__).resolve().parents[2]
SKILL_SRC = REPO / "skill"
TEMPLATE = SKILL_SRC / "templates" / "SKILL.md"
TARGETS = {
    "claude": Path.home() / ".claude" / "skills" / "moodle-bridge",
    "codex": Path.home() / ".codex" / "skills" / "moodle-bridge",
}
MARKER = re.compile(r"<!--\s*moodle-bridge-skill:\s*(\w+)\s*-->")
# Bloc le plus intérieur d'abord : son contenu ne contient pas d'autre « if »
BLOCK = re.compile(r"<!--\s*if\s+(!?)(\w+)\s*-->\n?((?:(?!<!--\s*if\s).)*?)<!--\s*endif\s*-->\n?", re.S)
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


class SkillError(Exception):
    pass


def python_command():
    """Commande Python à écrire dans le skill : celle qui fonctionne dans un terminal de ce système."""
    if system.IS_WINDOWS:
        return "py -3" if shutil.which("py") else f'"{Path(sys.executable).resolve()}"'
    return "python3" if shutil.which("python3") else str(Path(sys.executable).resolve())


def quote(path):
    return f'"{path}"'


def flags(cfg):
    engine = (cfg.data.get("classify") or {}).get("engine") or "none"
    return {
        "ai": "ai" in FEATURES,
        "ai_on": "ai" in FEATURES and engine != "none",
        "menubar": "menubar" in FEATURES and system.IS_MAC,
        "notes": "notes" in FEATURES,
        "zotero": bool(cfg.data.get("zotero", {}).get("enabled")),
        "texts": cfg.texts_dir is not None,
        "index": cfg.index_path is not None,
        "vault": cfg.vault_dir is not None,
        "mac": system.IS_MAC,
        "windows": system.IS_WINDOWS,
        "linux": system.IS_LINUX,
    }


def ai_ui():
    """Où l'utilisateur change lui-même d'IA, en plus de la commande `ai`."""
    if system.IS_WINDOWS:
        return f", ou un double-clic sur `{REPO / 'Choisir-IA.bat'}`"
    if system.IS_MAC:
        app = ", l'onglet IA de l'application" if "menubar" in FEATURES else ""
        return f"{app}, ou un double-clic sur `{REPO / 'Choisir-IA.command'}`"
    return ""


def values(cfg):
    engine = (cfg.data.get("classify") or {}).get("engine") or "none"
    labels = {"none": "aucun", "claude-cli": "Claude Code (`claude -p`)", "codex-cli": "Codex (`codex exec`)",
              "command": "commande personnalisée"}
    return {
        "CLI": f"{python_command()} -I {quote(REPO / 'scripts' / 'moodle_bridge.py')}",
        "CONFIG": quote(cfg.path),
        "CONFIG_PATH": str(cfg.path),
        "ENV": str(cfg.env_root),
        "STATE": str(cfg.state_path),
        "LOG": str(cfg.log_path),
        "INDEX": str(cfg.index_path) if cfg.index_path else "(aucun index configuré)",
        "VAULT": str(cfg.vault_dir) if cfg.vault_dir else "(aucun coffre configuré)",
        "AI_UI": ai_ui(),
        "TEXTS": str(cfg.texts_dir) if cfg.texts_dir else "(textes seulement dans l'état)",
        "REPO": str(REPO),
        "SITE": cfg.site,
        "SYSTEM": system.name(),
        "ENGINE": labels.get(engine, engine),
        "INTERVAL": str(cfg.interval_minutes),
        "SCHEDULER": {"macOS": "launchd", "Windows": "Planificateur de tâches", "Linux": "cron"}[system.name()],
        "SCHEDULE_LOG": str(schedule.log_path(cfg)),
        "CACHE": str(cfg.cache_dir),
        "INBOX": cfg.data["classify"]["inbox"],
        "EDITION": EDITION,
        "VERSION": __version__,
    }


def render(template, flag_values, replacements):
    """Texte du skill : blocs conditionnels résolus (de l'intérieur vers l'extérieur), repères remplacés."""
    def keep(match):
        negate, name, body = match.groups()
        if name not in flag_values:
            raise SkillError(f"condition inconnue dans le modèle : {name}")
        return body if bool(flag_values[name]) != bool(negate) else ""

    previous = None
    while previous != template:
        previous, template = template, BLOCK.sub(keep, template)

    def value(match):
        if match.group(1) not in replacements:
            raise SkillError(f"repère inconnu dans le modèle : {match.group(1)}")
        return replacements[match.group(1)]

    return re.sub(r"\n{3,}", "\n\n", PLACEHOLDER.sub(value, template))


def kind_of(dest):
    """« bootstrap », « installed », None (dossier vide ou absent) ou « foreign » (autre chose : ne pas toucher)."""
    skill = dest / "SKILL.md"
    if not dest.exists() or not any(dest.iterdir()):
        return None
    if (dest / "scripts" / "moodle_bridge.py").exists() or not skill.exists():
        return "foreign"
    match = MARKER.search(skill.read_text("utf-8", errors="replace"))
    return match.group(1) if match else "foreign"


def install(cfg, dest):
    """Écrit le skill dans `dest` ; remplace seulement un skill moodle-bridge d'amorçage ou déjà installé."""
    dest = Path(dest).expanduser()
    if dest.is_symlink():
        raise SkillError(f"{dest} est un lien symbolique : indique directement le dossier visé")
    kind = kind_of(dest)
    if kind == "foreign":
        raise SkillError(f"{dest} contient déjà autre chose qu'un skill moodle-bridge généré : rien n'a été modifié "
                         "(choisis un autre dossier avec --dest)")
    text = render(TEMPLATE.read_text("utf-8"), flags(cfg), values(cfg))
    dest.mkdir(parents=True, exist_ok=True)
    for folder in ("references", "agents"):
        target = dest / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SKILL_SRC / folder, target)
    (dest / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    return dest
