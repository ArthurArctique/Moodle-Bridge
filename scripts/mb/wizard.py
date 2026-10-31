"""Assistant d'installation : du site Moodle à la première synchronisation, une question à la fois.

Deux façons de l'utiliser :
- interactive (`install.py`, ou `moodle_bridge.py init`) : chaque étape propose une valeur par défaut ;
- non interactive (`init --answers réponses.json`), pour un agent qui a posé les questions dans sa conversation.
  Une réponse absente prend sa valeur par défaut ; la connexion à Moodle reste toujours à faire par
  l'utilisateur, dans son propre terminal (commande `token`).
"""

import getpass
import json
import re
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

from . import FEATURES, api, config, credentials, system, util, vault

try:
    from . import llm
except ImportError:  # édition sans modèle de langage
    llm = None

DEFAULT_RULES = (
    "Un dossier par type de séance, numéroté sur deux chiffres : CM/CM01, TD/TD03, TP/TP02. "
    "Les sujets d'examen et annales vont dans Examens/, les projets dans Projet/, "
    "et ce qui ne relève d'aucune séance dans Ressources/."
)
DEFAULT_PATTERNS = [r"(CM|TD|TP)/\1\d{2}", "Ressources", "Projet", "Examens"]
DEFAULT_VAULT = "Tableau de bord"
NO_VAULT = ("aucun", "none", "non", "-")
LOGIN_HINTS = {1: "password", 2: "browser", 3: "browser"}
# Fin d'adresse copiée depuis le navigateur, à retirer pour retrouver la racine du site
PAGE_SUFFIX = re.compile(r"/(login/index\.php|my/?|my/courses\.php|course/view\.php|index\.php|admin/.*)$")


class Abort(Exception):
    pass


# ---------------------------------------------------------------- réponses

class Prompter:
    """Questions posées dans le terminal ; en mode non interactif, renvoie les réponses données ou les défauts."""

    def __init__(self, answers=None, ask=input, ask_secret=getpass.getpass):
        self.answers = answers
        self.ask = ask
        self.ask_secret = ask_secret

    @property
    def interactive(self):
        return self.answers is None

    def text(self, key, prompt, default=None):
        if not self.interactive:
            return self.answers.get(key, default)
        suffix = f" [{default}]" if default not in (None, "") else ""
        try:
            value = self.ask(f"{prompt}{suffix} : ").strip()
        except (KeyboardInterrupt, EOFError):
            raise Abort("installation interrompue : rien n'a été écrit après la dernière étape terminée")
        return value or default

    def yes(self, key, prompt, default=True):
        if not self.interactive:
            return bool(self.answers.get(key, default))
        while True:
            value = (self.text(None, f"{prompt} {'[O/n]' if default else '[o/N]'}", "") or "").lower()
            if not value:
                return default
            if value in ("o", "oui", "y", "yes"):
                return True
            if value in ("n", "non", "no"):
                return False

    def choice(self, key, prompt, options, default):
        """`options` : liste de (valeur, libellé) ; l'utilisateur répond par le numéro ou la valeur."""
        if not self.interactive:
            return self.answers.get(key, default)
        print(prompt)
        for number, (value, label) in enumerate(options, start=1):
            print(f"  {number}. {label}{'  (par défaut)' if value == default else ''}")
        values = [value for value, _ in options]
        while True:
            raw = self.text(None, "Ton choix", str(values.index(default) + 1) if default in values else None)
            if raw and raw.isdigit() and 1 <= int(raw) <= len(options):
                return values[int(raw) - 1]
            if raw in values:
                return raw


def say(prompter, text=""):
    if prompter.interactive:
        print(text)


# ---------------------------------------------------------------- étapes réutilisables

def normalize_site(raw):
    """Racine du site à partir de ce que l'utilisateur colle (adresse d'une page, sans https…)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    path = PAGE_SUFFIX.sub("", parsed.path.rstrip("/"))
    return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")


def default_env():
    return Path.home() / "Moodle"


def course_code(shortname, taken=()):
    """Nom de dossier lisible et sûr tiré du nom court Moodle, unique parmi `taken`."""
    base = util.safe_name(re.sub(r"[\s/\\:]+", "-", shortname or "").strip("-"), 40) or "Cours"
    code, n = base, 2
    while code.lower() in {t.lower() for t in taken}:
        code, n = f"{base}-{n}", n + 1
    return code


def courses_config(courses, mode, existing=None, exclude=()):
    """Entrées `courses` de la configuration pour les cours suivis, sans toucher à celles qui existent."""
    result = dict(existing or {})
    taken = [c.get("code") or "" for c in result.values()]
    for course in sorted(courses, key=lambda c: c.get("fullname") or ""):
        key = str(course["id"])
        if key in result:
            continue
        if course["id"] in exclude or key in {str(e) for e in exclude}:
            result[key] = {"skip": True, "name": course.get("fullname")}
            continue
        code = course_code(course.get("shortname") or course.get("fullname"), taken)
        taken.append(code)
        result[key] = {"dest": code, "code": code, "mode": mode, "name": course.get("fullname")}
    return result


def base_config(site, keychain_service, engine, index_path, texts_dir, interval, mode, vault_dir=None):
    data = {
        "version": 1,
        "site": site,
        "keychain_service": keychain_service,
        "env_root": "..",
        "courses": {},
        "vault": {"dir": vault_dir, "title": "Moodle"},
        "index": {"path": index_path, "title": "Moodle"},
        "texts": {"dir": texts_dir},
        "schedule": {"interval_minutes": int(interval)},
    }
    if "ai" in FEATURES:
        data["classify"] = {"engine": engine.get("engine", "none"), "command": engine.get("command"),
                            "model": engine.get("model"), "rules": DEFAULT_RULES,
                            "new_dir_patterns": DEFAULT_PATTERNS}
    data["default_mode"] = mode
    return data


def token_service(site):
    return f"moodle-bridge:{urlparse(site).netloc}"


def check_token(cfg):
    """(nom complet de l'utilisateur, nom du site) si le jeton enregistré est accepté, sinon None."""
    try:
        token = credentials.read_token(cfg.data["keychain_service"])
    except credentials.TokenMissing:
        return None
    try:
        info = api.Client(cfg.site, token, retries=1, backoff=(2,)).call("core_webservice_get_site_info")
    except api.TokenError:
        return None
    return info.get("fullname"), info.get("sitename")


def obtain_token(cfg, method, prompter):
    service = cfg.data["keychain_service"]
    if method == "password":
        return credentials.obtain_with_password(cfg.site, service, prompter.ask, prompter.ask_secret)
    if method == "key":
        return credentials.obtain_by_paste(service, prompter.ask_secret)
    return credentials.obtain_with_browser(cfg.site, service, prompter.ask, prompter.ask_secret)


def write_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    util.write_json_atomic(path, data)
    # Ne jamais versionner l'état du pont par mégarde (dossier de travail suivi par git)
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")


# ---------------------------------------------------------------- assistant

def run(answers=None, actions=None, ask=input, ask_secret=getpass.getpass):
    """Installation complète ; `actions` fournit les étapes du point d'entrée (sync, planification, app, skill)."""
    p = Prompter(answers, ask, ask_secret)
    actions = actions or {}
    say(p, f"\n=== Moodle Bridge — installation ({system.name()}) ===\n")
    say(p, "Je vais te poser quelques questions ; Entrée garde la valeur proposée entre crochets.\n")

    # 1. Site
    site, public = None, None
    while not public:
        site = normalize_site(p.text("site", "Adresse de ton Moodle (ex. https://moodle.mon-universite.fr)"))
        if not site:
            if not p.interactive:
                raise Abort("réponse « site » manquante")
            continue
        try:
            public = api.public_config(site)
        except (urllib.error.URLError, OSError, ValueError, api.MoodleError) as exc:
            if not p.interactive:
                raise Abort(f"{site} ne répond pas comme un Moodle : {exc}")
            print(f"  → {site} ne répond pas comme un Moodle ({exc}). Vérifie l'adresse.\n")
    if not (public.get("enablewebservices") and public.get("enablemobilewebservice")):
        raise Abort(f"{public.get('sitename') or site} n'autorise pas l'appli mobile : le pont ne peut pas s'y "
                    "connecter (à demander au service informatique de l'établissement).")
    site = (public.get("wwwroot") or site).rstrip("/")
    login = public.get("typeoflogin")
    say(p, f"  ✓ {public.get('sitename') or site} — connexion "
           f"{'par identifiant et mot de passe Moodle' if login == 1 else 'par le navigateur (authentification unique)'}\n")

    # 2. Dossier de travail
    vaults = system.obsidian_vaults()
    options = [(str(default_env()), f"{default_env()} (nouveau dossier, à ouvrir comme coffre Obsidian)")]
    options += [(str(v / "Moodle"), f"{v / 'Moodle'} (dans ton coffre Obsidian « {v.name} »)") for v in vaults[:3]]
    if p.interactive and len(options) > 1:
        env = p.choice("env", "Où ranger tes cours ?", options + [("autre", "Autre dossier…")], options[0][0])
        if env == "autre":
            env = p.text("env", "Dossier", str(default_env()))
    else:
        env = p.text("env", "Dossier où ranger tes cours", str(default_env()))
    env = Path(env).expanduser().absolute()
    config_path = env / ".moodle-bridge" / "config.json"
    previous = None
    if config_path.exists():
        previous = json.loads(config_path.read_text("utf-8"))
        if not p.yes("reuse", f"Une installation existe déjà dans {env}. La mettre à jour (cours et réglages "
                              "existants conservés) ?", True):
            raise Abort("installation existante laissée telle quelle")

    # 3. Coffre Obsidian : notes générées, sans IA, dans un sous-dossier du dossier des cours
    prev = previous or {}
    vault_dir = p.text("vault", "Dossier des notes Obsidian générées (accueil, une note par matière, devoirs, "
                                "annonces…), dans le dossier des cours ; « aucun » pour s'en passer",
                       (prev.get("vault") or {}).get("dir") or DEFAULT_VAULT)
    if not vault_dir or str(vault_dir).strip().lower() in NO_VAULT:
        vault_dir = None
    # Ancien tableau de bord en un seul fichier et textes des cours (lus par les agents) : sans question
    given = p.answers or {}
    index_path = given.get("index", (prev.get("index") or {}).get("path"))
    texts_dir = given.get("texts_dir") or (prev.get("texts") or {}).get("dir") or ".moodle-bridge/textes"
    if system.obsidian_installed():
        if vault_dir:
            # Le dossier des cours devient le coffre : les liens vers les PDF s'ouvrent alors dans Obsidian
            if vault.find_root(env) == env and not (env / ".obsidian").exists():
                (env / ".obsidian").mkdir(parents=True, exist_ok=True)
            say(p, f"  ✓ Obsidian est installé : ouvre {vault.find_root(env)} comme coffre (« Ouvrir un dossier "
                   f"comme coffre »), puis la note « {vault_dir}/{vault.HOME} ».\n")
    else:
        say(p, "  ℹ Obsidian n'est pas installé : les notes Markdown seront créées quand même. Pour les lire "
               "confortablement, installe Obsidian (https://obsidian.md) ou ouvre le dossier dans VS Code.\n")

    # 4. Modèle de langage
    engine = {"engine": "none"}
    if "ai" in FEATURES and llm:
        found = llm.detect()
        options = [("none", "Aucune IA : rangement par section Moodle, rien n'est envoyé à un modèle")]
        if "claude-cli" in found:
            options.insert(0, ("claude-cli", f"Claude Code ({found['claude-cli']})"))
        if "codex-cli" in found:
            options.insert(len(options) - 1, ("codex-cli", f"Codex ({found['codex-cli']})"))
        options.append(("command", "Autre commande (Ollama, llm, Gemini CLI…)"))
        default = (previous or {}).get("classify", {}).get("engine") or options[0][0]
        say(p, "L'IA range les fichiers ambigus (CM/TD/TP…) et résume les annonces. Elle passe par la ligne de "
               "commande que tu utilises déjà (ton abonnement, aucune clé d'API).")
        choice = p.choice("engine", "Quelle IA utiliser ?", options, default)
        engine = {"engine": choice}
        if choice in ("claude-cli", "codex-cli"):
            engine["command"] = p.text("command", "Chemin de la commande", found.get(choice)) or found.get(choice)
            engine["model"] = p.text("model", "Modèle (Entrée : petit modèle par défaut)", None)
        elif choice == "command":
            raw = p.text("command", "Commande qui lit la demande sur l'entrée standard (ex. ollama run llama3.2)", None)
            engine["command"] = raw.split() if isinstance(raw, str) else raw
            if not engine["command"]:
                engine = {"engine": "none"}
    mode = "classify" if engine["engine"] != "none" else "mirror"
    if engine["engine"] != "none":
        mode = p.choice("mode", "Comment ranger les fichiers ?", [
            ("classify", "Par l'IA, en dossiers CM/TD/TP numérotés (fichiers incertains : dossier _a_classer)"),
            ("mirror", "Par section Moodle, sans IA (un dossier par section)")], "classify")

    # 5. Fréquence
    interval = None
    while interval is None:
        raw = p.text("interval", "Synchroniser toutes les combien de minutes ?",
                     (previous or {}).get("schedule", {}).get("interval_minutes") or 120)
        try:
            interval = max(15, int(raw))
        except (TypeError, ValueError):
            if not p.interactive:
                raise Abort(f"réponse « interval » invalide : {raw}")

    # 6. Configuration et connexion
    service = (previous or {}).get("keychain_service") or token_service(site)
    data = base_config(site, service, engine, index_path, texts_dir, interval, mode, vault_dir)
    if previous:
        data = config.deep_merge(previous, {k: v for k, v in data.items() if k != "courses"})
    write_config(config_path, data)
    cfg = config.load(config_path)
    say(p, f"  ✓ Configuration : {config_path}\n")

    who = check_token(cfg)
    if not who:
        # La connexion se fait toujours par l'utilisateur, dans son terminal : un agent ne la fait pas à sa place
        if not p.interactive:
            return finish(cfg, p, pending=["token"], actions=actions)
        method = p.choice("token_method", "Connexion à Moodle :", [
            ("password", "Identifiant et mot de passe Moodle (comptes propres au Moodle)"),
            ("browser", "Par le navigateur (CAS, Shibboleth, ENT… : authentification unique)"),
            ("key", "Coller la clé de la page « Clés de sécurité » de ton profil")], LOGIN_HINTS.get(login, "browser"))
        while not who:
            if obtain_token(cfg, method, p):
                who = check_token(cfg)
                if not who:
                    print("  → Moodle refuse ce jeton. Recommençons.\n")
            elif not p.yes(None, "Réessayer ?", True):
                return finish(cfg, p, pending=["token"], actions=actions)
    say(p, f"  ✓ Connecté à {who[1] or site} en tant que {who[0]}\n")

    # 7. Cours
    client = api.Client(cfg.site, credentials.read_token(cfg.data["keychain_service"]))
    info = client.call("core_webservice_get_site_info")
    courses = client.call("core_enrol_get_users_courses", userid=info["userid"])
    new = [c for c in courses if str(c["id"]) not in cfg.data["courses"]]
    exclude = []
    if new and p.interactive:
        print(f"{len(new)} cours trouvé(s) :")
        for number, course in enumerate(new, start=1):
            print(f"  {number:>2}. {util.nfc(course.get('fullname') or '')[:70]}")
        raw = p.text(None, "Numéros des cours à ignorer (ex. 3 7), Entrée pour tout suivre", "")
        picked = {int(n) for n in re.findall(r"\d+", raw or "")}
        exclude = [c["id"] for n, c in enumerate(new, start=1) if n in picked]
    elif new:
        exclude = list((answers or {}).get("exclude_courses") or [])
    data = json.loads(config_path.read_text("utf-8"))
    data["courses"] = courses_config(courses, mode, data.get("courses"), exclude)
    write_config(config_path, data)
    cfg = config.load(config_path)
    followed = [c for c in data["courses"].values() if not c.get("skip")]
    say(p, f"  ✓ {len(followed)} cours suivis, chacun dans son dossier sous {env}\n")
    return finish(cfg, p, actions=actions)


def finish(cfg, p, pending=(), actions=None):
    """Dernières étapes facultatives : première synchronisation, planification, application, skill."""
    actions = actions or {}
    done = {"config": str(cfg.path), "env": str(cfg.env_root), "pending": list(pending), "steps": []}
    if "token" in pending:
        done["next"] = f"connexion à faire par l'utilisateur, dans son terminal : token --config \"{cfg.path}\""
        say(p, f"\nIl reste à te connecter à Moodle, puis à relancer l'installation :\n  {done['next']}")
        return done
    if p.yes("first_sync", "Lancer la première synchronisation maintenant (peut prendre quelques minutes) ?", True):
        actions["sync"](cfg)
        done["steps"].append("sync")
    if p.yes("schedule", f"Synchroniser automatiquement toutes les {cfg.interval_minutes} min, rattrapées au "
                         "réveil de l'ordinateur ?", True):
        actions["schedule"](cfg)
        done["steps"].append("schedule")
    if "menubar" in FEATURES and system.IS_MAC and "menubar" in actions and p.yes(
            "menubar", "Installer l'application de barre des menus (état, nouveaux documents, annonces) ?", True):
        actions["menubar"](cfg)
        done["steps"].append("menubar")
    if "skill" in FEATURES and "skill" in actions:
        targets = p.choice("skill", "Installer le skill pour tes agents IA (ils sauront lire et utiliser tes données) ?", [
            ("claude", "Claude Code"), ("codex", "Codex"), ("both", "Les deux"), ("none", "Non")], "none")
        if targets != "none":
            actions["skill"](cfg, ["claude", "codex"] if targets == "both" else [targets] if isinstance(targets, str)
                             else list(targets))
            done["steps"].append("skill")
    index = cfg.index_path
    say(p, "\n=== Terminé ===")
    say(p, f"Tes cours : {cfg.env_root}")
    if cfg.vault_dir:
        say(p, f"Coffre Obsidian : {cfg.vault_dir / (vault.HOME + '.md')}")
    if index:
        say(p, f"Tableau de bord : {index}")
    say(p, f"Réglages : {cfg.path}")
    return done
