"""Choix de l'IA : moteur (Claude Code, Codex, Gemini, autre commande ou aucune) et modèle, sans éditer de JSON.

Le pont n'a jamais de clé d'API : il passe par une ligne de commande déjà installée et connectée. Ce module dit
lesquelles sont présentes et connectées, liste leurs modèles, change le réglage par défaut (`classify`, que les
résumés d'annonces et les énigmes des notes suivent) et lance la connexion. Il sert à l'onglet IA de
l'application de barre des menus (`ai status --json`, `ai set`, `ai login`), aux lanceurs `Choisir-IA` du dépôt
et au terminal (`ai`, sans action : questions pas à pas).
"""

import functools
import json
import os
import subprocess
import time
from pathlib import Path

from . import config, llm, system, util

LABELS = {"claude-cli": "Claude Code", "codex-cli": "Codex", "gemini-cli": "Gemini", "command": "Autre commande",
          "none": "Aucune IA"}
HINTS = {"claude-cli": "abonnement Claude", "codex-cli": "compte ChatGPT",
         "gemini-cli": "compte Google, par Antigravity (agy) ou Gemini CLI",
         "command": "Ollama, llm… : la demande arrive sur l'entrée standard",
         "none": "rangement par section, pas de résumés ni d'énigmes"}
CLAUDE_MODELS = [
    {"id": "haiku", "label": "Haiku 5.5", "hint": "rapide et économique, recommandé"},
    {"id": "sonnet", "label": "Sonnet 5.5", "hint": "plus fin, plus lent"},
    {"id": "opus", "label": "Opus", "hint": "le plus capable, le plus coûteux"},
]
# Alias de Gemini CLI, qui suivent ses nouvelles versions ; Antigravity, lui, liste les modèles du compte
GEMINI_MODELS = [
    {"id": "flash-lite", "label": "Flash-Lite", "hint": "rapide et économique, recommandé"},
    {"id": "flash", "label": "Flash", "hint": "équilibré"},
    {"id": "pro", "label": "Pro", "hint": "le plus capable, plus lent"},
]
# Antigravity et Gemini CLI n'ont pas de commande de connexion : la première ouverture propose de se connecter
LOGIN_ARGS = {"claude-cli": ["auth", "login"], "codex-cli": ["login"], "gemini-cli": []}
INSTALL = {
    "claude-cli": "https://docs.claude.com/claude-code (macOS, Linux : curl -fsSL https://claude.ai/install.sh | bash)",
    "codex-cli": "https://github.com/openai/codex (npm install -g @openai/codex)",
    "gemini-cli": "https://antigravity.google (Antigravity CLI, commande agy) ; Gemini CLI pour une offre payante "
                  "ou une clé d'API : npm install -g @google/gemini-cli",
}
# Emplacements habituels des commandes, que la tâche planifiée et l'application ne trouvent pas dans leur PATH
EXTRA_DIRS = [Path.home() / d for d in (".local/bin", ".claude/local", ".npm-global/bin", ".bun/bin", ".volta/bin")] + [
    Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
if system.IS_WINDOWS:
    EXTRA_DIRS.append(Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "npm")
REGISTRY = "installs.json"
TEST_PROMPT = 'Réponds uniquement par cet objet JSON, sans rien autour : {"ok": true}'


class AiError(Exception):
    pass


# ---------------------------------------------------------------- commandes présentes

def locate(engine, configured=None):
    """Chemin de la commande d'un moteur : celle de la configuration, celle du PATH, ou un emplacement habituel."""
    names = llm.COMMANDS.get(engine, ())
    for candidate in (configured,) + names:
        found = system.find_command(candidate) if isinstance(candidate, str) else None
        if found:
            return found
    for name in names:
        for folder in EXTRA_DIRS:
            for suffix in ((".cmd", ".exe", "") if system.IS_WINDOWS else ("",)):
                path = folder / f"{name}{suffix}"
                if path.is_file():
                    return str(path)
    return None


def _quick(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=system.NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None


@functools.lru_cache(maxsize=4)
def _agy_models(path):
    """(code de sortie, sortie) de `agy models`, gardés pour la durée du processus : l'état de connexion et la liste
    des modèles posent la même question."""
    proc = _quick([path, "models"], timeout=30)
    return (None, "") if proc is None else (proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def antigravity_models(path):
    """Modèles du compte selon `agy models` (une ligne « identifiant<TAB>nom » chacun), None si la commande échoue."""
    code, output = _agy_models(path)
    if code != 0:
        return None
    rows = []
    for line in output.splitlines():
        slug, tab, label = line.partition("\t")
        if tab and slug.strip() and " " not in slug.strip():
            rows.append({"id": slug.strip(), "label": label.strip() or slug.strip(), "hint": ""})
    return rows


def logged_in(engine, path):
    """Vrai ou faux si la commande sait le dire, None sinon. De `claude auth status`, seul `loggedIn` est lu."""
    if not path or engine not in LOGIN_ARGS:
        return None
    if engine == "gemini-cli":
        if llm.is_antigravity(path):
            # agy n'a pas de commande d'état : la liste des modèles du compte ne s'obtient que connecté
            if antigravity_models(path):
                return True
            code, output = _agy_models(path)
            asks = ("sign in", "log in", "login", "not authenticated", "unauthenticated", "credentials")
            return False if code and any(word in output.lower() for word in asks) else None
        # Gemini CLI : identifiants enregistrés à la connexion, ou clé d'API (le fichier n'est pas lu)
        keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
        saved = (Path.home() / ".gemini" / "oauth_creds.json").exists()
        return True if saved or any(os.environ.get(k) for k in keys) else None
    if engine == "claude-cli":
        proc = _quick([path, "auth", "status"])
        try:
            return bool(json.loads(proc.stdout).get("loggedIn")) if proc else None
        except ValueError:
            return None
    proc = _quick([path, "login", "status"])
    return None if proc is None else proc.returncode == 0


def models(engine, path):
    """Modèles proposés : alias de Claude Code, liste publique de Codex (`codex debug models`), modèles du compte
    Antigravity (`agy models`, le plus léger des Flash d'abord) ou alias de Gemini CLI."""
    if engine == "claude-cli":
        return [dict(m) for m in CLAUDE_MODELS]
    if engine == "gemini-cli" and path:
        if not llm.is_antigravity(path):
            return [dict(m) for m in GEMINI_MODELS]
        rows = antigravity_models(path) or []
        light = next((m for m in rows if "flash" in m["id"] and m["id"].endswith("-low")),
                     next((m for m in rows if "flash" in m["id"]), None))
        if light:
            light["hint"] = "rapide et économique, recommandé"
            rows = [light] + [m for m in rows if m is not light]
        return rows
    if engine == "codex-cli" and path:
        proc = _quick([path, "debug", "models"])
        try:
            listed = json.loads(proc.stdout).get("models", []) if proc and proc.returncode == 0 else []
        except ValueError:
            listed = []
        rows = [{"id": m["slug"], "label": m.get("display_name") or m["slug"], "hint": m.get("description") or ""}
                for m in listed if m.get("slug") and m.get("visibility", "list") == "list"]
        return [{"id": None, "label": "Modèle par défaut de Codex", "hint": "celui de ton compte"}] + rows
    return []


# ---------------------------------------------------------------- état et réglage

def current(cfg):
    conf = cfg.data["classify"]
    engine = (conf.get("engine") or "none").lower()
    model = conf.get("model") or (llm.DEFAULT_MODELS.get(engine) if engine != "none" else None)
    command = conf.get("command")
    return {"engine": engine, "label": LABELS.get(engine, engine), "model": model,
            "model_is_default": not conf.get("model"),
            "command": command if isinstance(command, str) or command is None else " ".join(command)}


def overrides(cfg):
    """Tâches qui ont leur propre moteur ou modèle au lieu de suivre le réglage par défaut."""
    sections = {"summaries": cfg.data.get("summaries") or {}, "notes.mystery": (cfg.data.get("notes") or {}).get("mystery") or {}}
    return {name: {k: section[k] for k in ("engine", "model", "command") if section.get(k)}
            for name, section in sections.items() if any(section.get(k) for k in ("engine", "model", "command"))}


def engines(now=None, with_models=True):
    """Moteurs proposés, installés ou non, connectés ou non, avec leurs modèles ; `now` : le réglage actuel, s'il existe."""
    now = now or {"engine": "none", "model": None, "command": None}
    found = []
    for engine in ("claude-cli", "codex-cli", "gemini-cli"):
        path = locate(engine, now["command"] if now["engine"] == engine else None)
        listed = models(engine, path) if with_models else []
        if now["engine"] == engine and now["model"] and all(m["id"] != now["model"] for m in listed):
            listed.append({"id": now["model"], "label": now["model"], "hint": "réglage actuel"})
        found.append({"id": engine, "label": LABELS[engine], "hint": HINTS[engine], "path": path,
                      "installed": bool(path), "logged_in": logged_in(engine, path), "models": listed,
                      "install": INSTALL[engine]})
    found.append({"id": "command", "label": LABELS["command"], "hint": HINTS["command"], "path": None,
                  "installed": now["engine"] == "command", "logged_in": None, "models": [], "install": None})
    found.append({"id": "none", "label": LABELS["none"], "hint": HINTS["none"], "path": None,
                  "installed": True, "logged_in": None, "models": [], "install": None})
    return found


def status(cfg, with_models=True):
    now = current(cfg)
    return {"config": str(cfg.path), "current": now, "overrides": overrides(cfg), "engines": engines(now, with_models)}


def apply(config_path, engine, model=None, command=None):
    """Change le moteur et le modèle par défaut dans config.json, et fait suivre les résumés et les énigmes.

    Renvoie les réglages propres à une tâche qui ont été retirés. Le reste du fichier est conservé tel quel.
    """
    engine = (engine or "none").lower()
    if engine not in llm.ENGINES:
        raise AiError(f"moteur inconnu : {engine} (possibles : {', '.join(llm.ENGINES)})")
    path = Path(config_path)
    data = json.loads(path.read_text("utf-8"))
    conf = data.setdefault("classify", {})
    if engine == "none":
        conf.update(engine="none", model=None)
    else:
        if engine == "command":
            if not command and conf.get("engine") == "command":
                command = conf.get("command")  # même commande, autre modèle
            if not command:
                raise AiError("moteur « command » : indiquer la commande, par exemple « ollama run {model} »")
        else:
            command = locate(engine, command)
            if not command:
                raise AiError(f"{LABELS[engine]} n'est pas installé : {INSTALL[engine]}")
        if conf.get("engine") != engine:
            conf["args"] = None  # options propres à l'ancien moteur
        conf.update(engine=engine, model=model or None, command=command)
    cleared = []
    for name, section in (("summaries", data.get("summaries")), ("notes.mystery", (data.get("notes") or {}).get("mystery"))):
        if isinstance(section, dict):
            for key in ("engine", "model", "command", "args"):
                if section.get(key):
                    cleared.append(f"{name}.{key}")
                    section[key] = None
    util.write_json_atomic(path, data)
    config.load(path)  # le fichier doit rester lisible
    return cleared


def login(engine, path=None):
    """Connexion interactive dans le terminal courant (`claude auth login`, `codex login`, première ouverture d'agy
    ou de gemini)."""
    path = locate(engine, path)
    if engine not in LOGIN_ARGS:
        raise AiError(f"{LABELS.get(engine, engine)} : pas de connexion à faire")
    if not path:
        raise AiError(f"{LABELS[engine]} n'est pas installé : {INSTALL[engine]}")
    if not LOGIN_ARGS[engine]:
        print(f"{Path(path).name} s'ouvre : connecte-toi avec ton compte Google, puis quitte-le (Ctrl+C).\n")
    return subprocess.call([path] + LOGIN_ARGS[engine])


def test(cfg):
    """Un appel minimal au moteur configuré : réponse, durée et modèle réellement servi."""
    settings = llm.settings_for({}, cfg.data["classify"])
    engine = llm.make_engine(settings, "Tu es appelé par un script. Réponds uniquement par l'objet JSON demandé.")
    if engine is None:
        return {"ok": True, "engine": "none", "message": "Aucune IA : rien à tester."}
    start = time.time()
    try:
        answer = engine.ask(TEST_PROMPT)
    except llm.EngineError as exc:
        return {"ok": False, "engine": settings["engine"], "message": str(exc)}
    seconds = round(time.time() - start, 1)
    ok = '"ok"' in answer and "true" in answer.lower()
    used = ", ".join(sorted(engine.models_used)) or settings.get("model") or "par défaut"
    return {"ok": ok, "engine": settings["engine"], "model": used, "seconds": seconds,
            "message": f"Réponse en {seconds} s (modèle : {used})." if ok else f"Réponse inattendue : {answer.strip()[:200]}"}


# ---------------------------------------------------------------- installations connues

def registry_path():
    return system.data_root() / REGISTRY


def remember(cfg):
    """Note l'emplacement de cette installation, pour que les lanceurs la trouvent sans --config."""
    path = registry_path()
    try:
        known = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        known = []
    if str(cfg.path) in known:
        return
    known = [p for p in known if Path(p).exists()] + [str(cfg.path)]
    path.parent.mkdir(parents=True, exist_ok=True)
    util.write_json_atomic(path, known)


def known_configs():
    try:
        known = json.loads(registry_path().read_text("utf-8"))
    except (OSError, ValueError):
        known = []
    default = Path.home() / "Moodle" / ".moodle-bridge" / "config.json"
    paths = [Path(p) for p in known] + [default]
    seen, found = set(), []
    for path in paths:
        if path.exists() and str(path) not in seen:
            seen.add(str(path))
            found.append(path)
    return found


# ---------------------------------------------------------------- questions pas à pas

def _pick(ask, question, options, default):
    """options : [(valeur, texte)] ; renvoie la valeur choisie (Entrée : `default`)."""
    for number, (_value, text) in enumerate(options, 1):
        print(f"  {number}. {text}")
    numbers = {str(n): value for n, (value, _text) in enumerate(options, 1)}
    default_number = next((n for n, v in numbers.items() if v == default), "1")
    while True:
        answer = ask(f"{question} [{default_number}] : ").strip() or default_number
        if answer in numbers:
            return numbers[answer]
        print("  Réponds par un des numéros proposés.")


def _yes(ask, question, default=True):
    answer = ask(f"{question} [{'O/n' if default else 'o/N'}] : ").strip().lower()
    return default if not answer else answer.startswith(("o", "y"))


def choose_config(ask=input):
    found = known_configs()
    if len(found) == 1:
        return found[0]
    if found:
        print("Plusieurs installations de Moodle Bridge :")
        return _pick(ask, "Laquelle régler ?", [(p, str(p.parent.parent)) for p in found], found[0])
    raw = ask("Dossier des cours (celui qui contient .moodle-bridge) : ").strip().strip('"')
    path = Path(raw).expanduser() / ".moodle-bridge" / "config.json"
    if not path.exists():
        raise AiError(f"aucune installation dans {raw}")
    return path


def interactive(config_path, ask=input):
    cfg = config.load(config_path)
    print("Moodle Bridge — choix de l'IA\n")
    print(f"Installation : {cfg.env_root}")
    print("Je regarde les IA installées sur cette machine…\n")
    info = status(cfg)
    now = info["current"]
    print(f"Réglage actuel : {now['label']}" + (f", modèle {now['model']}" if now["model"] else "") + "\n")
    options = []
    for engine in info["engines"]:
        state = "" if engine["id"] not in LOGIN_ARGS else (
            "non installé" if not engine["installed"] else "connecté" if engine["logged_in"]
            else "non connecté" if engine["logged_in"] is False else "")
        options.append((engine["id"], f"{engine['label']} — {engine['hint']}" + (f" [{state}]" if state else "")))
    choice = _pick(ask, "Quelle IA utiliser ?", options, now["engine"])
    engine = next(e for e in info["engines"] if e["id"] == choice)

    command = None
    if choice in LOGIN_ARGS:
        if not engine["installed"]:
            print(f"\n{engine['label']} n'est pas installé. Installation : {engine['install']}")
            print("Relance ce choix une fois la commande installée.")
            return 1
        if engine["logged_in"] is False:
            print(f"\n{engine['label']} est installé mais pas connecté.")
            if _yes(ask, "Se connecter maintenant ?"):
                login(choice, engine["path"])
                engine["logged_in"] = logged_in(choice, engine["path"])
            if not engine["logged_in"]:
                print("Pas connecté : le réglage est enregistré quand même, la connexion pourra se faire plus tard.")
    elif choice == "command":
        command = ask("Commande (exemple : ollama run {model}) : ").strip()
        if not command:
            print("Aucune commande : rien n'est changé.")
            return 1

    model = None
    if choice == "command":
        model = ask("Modèle, remplacé dans {model} (Entrée : aucun) : ").strip() or None
    elif engine["models"]:
        print()
        listed = [(m["id"], m["label"] + (f" — {m['hint']}" if m.get("hint") else "")) for m in engine["models"]]
        listed.append(("__other__", "Autre (saisir son nom)"))
        default = now["model"] if now["engine"] == choice else engine["models"][0]["id"]
        model = _pick(ask, "Quel modèle ?", listed, default)
        if model == "__other__":
            model = ask("Nom du modèle : ").strip() or None

    cleared = apply(cfg.path, choice, model, command or engine["path"])
    shown = LABELS[choice] + (f", modèle {model}" if model else "")
    print(f"\nEnregistré : {shown}.")
    if cleared:
        print("Les résumés d'annonces et les énigmes des notes suivent désormais ce réglage.")
    if choice != "none" and _yes(ask, "Faire un essai maintenant ?"):
        result = test(config.load(cfg.path))
        print(("✓ " if result["ok"] else "✗ ") + result["message"])
    return 0
