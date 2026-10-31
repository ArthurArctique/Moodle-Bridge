"""Modèles de langage appelés par leur ligne de commande, sans clé d'API : Claude Code, Codex, ou toute
commande qui lit la demande sur son entrée standard (Ollama, llm, Gemini CLI…).

Le pont n'utilise un modèle que pour trois tâches courtes, toujours avec une consigne qui exige une réponse
JSON : choisir le dossier d'un fichier ambigu, résumer une annonce, habiller une note « mystère ». Chaque
appel se fait dans un dossier temporaire vide, sans outils quand la commande le permet.
"""

import json
import shlex
import subprocess
import tempfile
from pathlib import Path

from . import system

ENGINES = ("none", "claude-cli", "codex-cli", "command")
# Modèle par défaut de chaque moteur : un petit modèle suffit (None : celui que la commande choisit)
DEFAULT_MODELS = {"claude-cli": "haiku", "codex-cli": None, "command": None}
DEFAULT_COMMANDS = {"claude-cli": "claude", "codex-cli": "codex"}


class EngineError(Exception):
    pass


def _resolve(command):
    found = system.find_command(command)
    if not found:
        raise EngineError(f"commande introuvable : {command}")
    return found


def _run(args, prompt, timeout, cwd, what):
    try:
        proc = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              timeout=timeout, cwd=cwd, creationflags=system.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineError(f"{what} indisponible : {exc}") from exc
    if proc.returncode != 0:
        raise EngineError(f"{what} a échoué ({proc.returncode}) : {(proc.stderr or proc.stdout).strip()[-300:]}")
    return proc


class ClaudeCli:
    """`claude -p` (Claude Code) : sans outils, skills, serveurs MCP ni réglages ; la consigne remplace la sienne."""

    name = "claude-cli"

    def __init__(self, settings, system_prompt):
        self.command = settings.get("command") or DEFAULT_COMMANDS[self.name]
        self.model = settings.get("model") or DEFAULT_MODELS[self.name]
        self.timeout = int(settings.get("timeout_s") or 300)
        self.system_prompt = system_prompt
        self.models_used = set()  # modèles réellement servis, pour le journal

    def ask(self, prompt):
        args = [_resolve(self.command), "-p", "--model", self.model, "--system-prompt", self.system_prompt,
                "--tools", "", "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "",
                "--output-format", "json", "--no-session-persistence"]
        with tempfile.TemporaryDirectory(prefix="moodle-bridge-") as cwd:
            proc = _run(args, prompt, self.timeout, cwd, "claude")
        try:
            answer = json.loads(proc.stdout)
        except ValueError as exc:
            raise EngineError("sortie de claude illisible") from exc
        if answer.get("is_error"):
            raise EngineError(f"claude : {str(answer.get('result'))[:300]}")
        self.models_used.update(answer.get("modelUsage") or {})
        return answer.get("result", "")


class CodexCli:
    """`codex exec` (Codex d'OpenAI) : bac à sable en lecture seule, session éphémère, dernier message seulement."""

    name = "codex-cli"

    def __init__(self, settings, system_prompt):
        self.command = settings.get("command") or DEFAULT_COMMANDS[self.name]
        self.model = settings.get("model") or DEFAULT_MODELS[self.name]
        self.timeout = int(settings.get("timeout_s") or 300)
        self.extra = list(settings.get("args") or ["-c", 'model_reasoning_effort="low"'])
        self.system_prompt = system_prompt
        self.models_used = {self.model} if self.model else set()

    def ask(self, prompt):
        with tempfile.TemporaryDirectory(prefix="moodle-bridge-") as cwd, \
                tempfile.TemporaryDirectory(prefix="moodle-bridge-out-") as out_dir:
            answer = Path(out_dir) / "answer.txt"
            args = [_resolve(self.command), "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                    "--color", "never", "-C", cwd, "-o", str(answer)]
            if self.model:
                args += ["-m", self.model]
            args += self.extra + ["-"]
            _run(args, f"{self.system_prompt}\n\n{prompt}", self.timeout, cwd, "codex")
            try:
                return answer.read_text("utf-8")
            except OSError as exc:
                raise EngineError("codex n'a rendu aucune réponse") from exc


class Command:
    """Commande quelconque : la demande arrive sur l'entrée standard, la réponse est lue sur la sortie standard.

    `command` est une liste d'arguments (ou une chaîne découpée comme dans un terminal) ; « {model} » y est
    remplacé par le modèle choisi. Exemple : ["ollama", "run", "{model}"].
    """

    name = "command"

    def __init__(self, settings, system_prompt):
        command = settings.get("command")
        if not command:
            raise EngineError("moteur « command » : indiquer la commande (classify.command)")
        self.args = shlex.split(command, posix=not system.IS_WINDOWS) if isinstance(command, str) else list(command)
        self.model = settings.get("model") or ""
        self.timeout = int(settings.get("timeout_s") or 300)
        self.system_prompt = system_prompt
        self.models_used = {self.model} if self.model else set()

    def ask(self, prompt):
        args = [arg.replace("{model}", self.model) for arg in self.args]
        args[0] = _resolve(args[0])
        with tempfile.TemporaryDirectory(prefix="moodle-bridge-") as cwd:
            return _run(args, f"{self.system_prompt}\n\n{prompt}", self.timeout, cwd, Path(self.args[0]).name).stdout


CLASSES = {"claude-cli": ClaudeCli, "codex-cli": CodexCli, "command": Command}


def settings_for(section, base):
    """Réglages d'une tâche (résumés, mystère) : les siens, complétés par ceux du classement s'ils sont vides."""
    engine = (section.get("engine") or base.get("engine") or "none").lower()
    same = engine == (base.get("engine") or "none").lower()
    return {
        "engine": engine,
        "command": section.get("command") or (base.get("command") if same else None),
        "model": section.get("model") or (base.get("model") if same else None),
        "args": section.get("args") or (base.get("args") if same else None),
        "timeout_s": section.get("timeout_s") or base.get("timeout_s"),
    }


def make_engine(settings, system_prompt):
    """Moteur prêt à l'emploi, ou None si aucun n'est configuré."""
    engine = (settings.get("engine") or "none").lower()
    if engine == "none":
        return None
    if engine not in CLASSES:
        raise EngineError(f"moteur inconnu : {engine} (possibles : {', '.join(ENGINES)})")
    return CLASSES[engine](settings, system_prompt)


def detect():
    """Lignes de commande d'IA présentes sur la machine, pour l'assistant d'installation."""
    return {engine: system.find_command(command) for engine, command in DEFAULT_COMMANDS.items()
            if system.find_command(command)}
