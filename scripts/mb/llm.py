"""Modèles de langage appelés par leur ligne de commande, sans clé d'API : Claude Code, Codex, Gemini (Antigravity
CLI ou Gemini CLI), ou toute commande qui lit la demande sur son entrée standard (Ollama, llm…).

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

ENGINES = ("none", "claude-cli", "codex-cli", "gemini-cli", "command")
# Modèle par défaut de chaque moteur : un petit modèle suffit (None : celui que la commande choisit)
DEFAULT_MODELS = {"claude-cli": "haiku", "codex-cli": None, "gemini-cli": None, "command": None}
# Claude Code fait suivre ses alias avec retard : en 2.1.278, « haiku » sert encore Haiku 4.5 et « sonnet »
# Sonnet 5. Le pont demande donc ces modèles par leur nom complet ; « opus » reste un alias, Opus 5.5 n'étant
# pas accepté par toutes les versions de Claude Code. Un nom complet choisi par l'utilisateur passe tel quel.
CLAUDE_ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
# Commandes de chaque moteur, la préférée d'abord. Gemini : Antigravity CLI (agy) a remplacé Gemini CLI pour les
# comptes Google gratuits et Google One ; Gemini CLI reste celle des offres payantes et des clés d'API.
COMMANDS = {"claude-cli": ("claude",), "codex-cli": ("codex",), "gemini-cli": ("agy", "gemini")}
DEFAULT_COMMANDS = {engine: names[0] for engine, names in COMMANDS.items()}


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
        model = settings.get("model") or DEFAULT_MODELS[self.name]
        self.model = CLAUDE_ALIASES.get(model.lower(), model)
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


def is_antigravity(command):
    """Vrai pour Antigravity CLI (`agy`), faux pour Gemini CLI (`gemini`) : elles se pilotent différemment."""
    return Path(str(command)).stem.lower() == "agy"


class GeminiCli:
    """Gemini de Google, dans un dossier vide, la demande sur l'entrée standard (jamais dans la liste des processus).

    Antigravity CLI (`agy`) : un tour en flux JSON (`--input-format stream-json`), en bac à sable ; sans terminal
    pour les accepter, les actions soumises à autorisation (écriture, commande) n'ont pas lieu. Chaque appel reste
    dans l'historique d'Antigravity.
    Gemini CLI (`gemini`) : mode sans interface (entrée standard redirigée), sortie JSON ; en mode sans interface,
    écritures et commandes sont refusées par défaut.
    """

    name = "gemini-cli"

    def __init__(self, settings, system_prompt):
        installed = next(filter(None, map(system.find_command, COMMANDS[self.name])), None)
        self.command = settings.get("command") or installed or DEFAULT_COMMANDS[self.name]
        self.model = settings.get("model") or DEFAULT_MODELS[self.name]
        self.timeout = int(settings.get("timeout_s") or 300)
        self.extra = list(settings.get("args") or [])
        self.system_prompt = system_prompt
        self.models_used = {self.model} if self.model else set()

    def ask(self, prompt):
        command = _resolve(self.command)
        text = f"{self.system_prompt}\n\n{prompt}"
        model = ["--model", self.model] if self.model else []
        with tempfile.TemporaryDirectory(prefix="moodle-bridge-") as cwd:
            if is_antigravity(command):
                args = [command, "--input-format", "stream-json", "--output-format", "stream-json", "--sandbox",
                        "--disable-slash-commands"] + model + self.extra
                message = json.dumps({"event": "user", "message": {"role": "user", "content": text}},
                                     ensure_ascii=False)
                proc = _run(args, message + "\n", self.timeout, cwd, "agy")
                return self._antigravity_answer(proc.stdout)
            proc = _run([command, "--output-format", "json"] + model + self.extra, text, self.timeout, cwd, "gemini")
        try:
            answer = json.loads(proc.stdout)
        except ValueError as exc:
            raise EngineError("sortie de gemini illisible") from exc
        error = answer.get("error")
        if error:
            raise EngineError(f"gemini : {str(error.get('message') if isinstance(error, dict) else error)[:300]}")
        self.models_used.update((answer.get("stats") or {}).get("models") or {})
        return answer.get("response") or ""

    @staticmethod
    def _antigravity_answer(stdout):
        result = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") == "result":
                result = event.get("result") or {}
        if result is None:
            raise EngineError("agy n'a rendu aucune réponse")
        if result.get("status") != "SUCCESS" or result.get("finished_reason"):
            reason = result.get("error") or result.get("finished_reason") or result.get("status")
            raise EngineError(f"agy : {str(reason)[:300]}")
        return result.get("response") or ""


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


CLASSES = {"claude-cli": ClaudeCli, "codex-cli": CodexCli, "gemini-cli": GeminiCli, "command": Command}


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
    found = {}
    for engine, names in COMMANDS.items():
        path = next(filter(None, map(system.find_command, names)), None)
        if path:
            found[engine] = path
    return found
