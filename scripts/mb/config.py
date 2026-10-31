"""Configuration d'un environnement : <env>/.moodle-bridge/config.json, chemins dérivés et valeurs par défaut."""

import copy
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import system

DEFAULTS = {
    "version": 1,
    "site": None,
    "keychain_service": "moodle-token",
    "env_root": "..",
    "courses": {},
    # Mode des cours ajoutés par `courses --add-all` : mirror (sections Moodle) ou classify (modèle de langage)
    "default_mode": "mirror",
    "exclude": {
        # Activités dont les « fichiers » sont des pages web Moodle et non des documents
        "modnames": ["page", "book", "lesson", "scorm", "h5pactivity", "imscp"],
        # Listés dans l'index mais jamais téléchargés
        "list_only_mimetypes": ["video/", "audio/"],
        "max_size_mb": 200,
    },
    "classify": {
        # none, claude-cli (Claude Code), codex-cli (Codex) ou command (toute commande lisant l'entrée standard)
        "engine": "none",
        # null : `claude` ou `codex` trouvé dans le PATH ; un chemin absolu est plus sûr pour la tâche planifiée
        "command": None,
        # null : le petit modèle par défaut du moteur (voir llm.DEFAULT_MODELS)
        "model": None,
        "args": None,
        "min_confidence": 0.8,
        "timeout_s": 300,
        "inbox": "_a_classer",
        "rules": "",
        "context_files": [],
        "new_dir_patterns": [r"(CM|TD|TP)/\1\d{2}", "Ressources", "Projet"],
    },
    "overrides": {},
    # Supports de cours destinés à une bibliothèque Zotero : mis de côté puis importés par un agent,
    # jamais rangés dans l'arborescence
    "zotero": {
        "enabled": False,
        "token": "@zotero",
        "staging": ".moodle-bridge/a_importer_zotero",
        "search_roots": [],
    },
    # Textes de Moodle conservés : résumés de sections, étiquettes, pages, consignes des devoirs, annonces
    "texts": {
        "enabled": True,
        # Un fichier Markdown par cours (relatif à env_root) ; null : textes seulement dans l'état et l'index
        "dir": ".moodle-bridge/textes",
        # Modalités de contrôle mises en évidence : titre de la section (ou de l'activité) ou contenu du texte
        "mcc_title": r"modalit\w*\s+de\s+contr[ôo]le|\bMCCC?\b",
        "mcc_content": r"formule\s+de\s+calcul|calcul\s+de\s+la\s+note",
    },
    # Coffre Obsidian généré : accueil, une note par matière, devoirs, échéances, annonces, notes
    "vault": {
        # Dossier des notes générées (relatif à env_root) ; null : pas de coffre
        "dir": None,
        "title": "Moodle",
    },
    # Ancien tableau de bord en un seul fichier ; null : aucun
    "index": {
        "path": None,
        "title": "Moodle",
        "news_days": 7,
        "announcements_per_course": 5,
    },
    # Résumé des annonces récentes (index et application de barre des menus) ; enabled null : actif si le
    # classement passe déjà par un modèle. engine, command et model vides : ceux du classement
    "summaries": {"enabled": None, "engine": None, "command": None, "model": None, "timeout_s": 120},
    "schedule": {"interval_minutes": 120},
    "notify": True,
    # Notes : vérification déterministe et fréquente, indépendante de la synchronisation des documents
    "notes": {
        "enabled": False,
        "interval_minutes": 15,
        # Activités dont la note n'en est pas une (quiz d'entraînement, présence, SCORM…) : jamais suivies
        "exclude_modules": ["quiz", "attendance", "scorm", "h5pactivity", "lesson"],
        # Motifs (expressions régulières) sur le nom de l'élément de note
        "exclude_items": [],
        "include_items": [],  # prioritaire sur les exclusions
        # Emoji et couleur de la notification mystère, choisis par un modèle ; sans lui, 🎁 gris
        "mystery": {"enabled": True, "engine": None, "command": None, "model": None, "timeout_s": 120},
        "workbook": {
            "path": None,  # classeur (.xlsx), relatif à env_root ; null : aucun report
            "sheet": "Notes",
            # Python disposant d'openpyxl ; null : celui que crée `notes setup`
            "python": None,
            # "<courseid>/<itemid>" → {"element": "ABC", "note": 1, "item": "<nom Moodle, pour mémoire>"}
            "map": {},
        },
    },
}


def deep_merge(base, extra):
    merged = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigError(Exception):
    pass


@dataclass
class Config:
    data: dict
    path: Path
    env_root: Path
    cache_dir: Path
    slug: str
    dir: Path = field(init=False)

    def __post_init__(self):
        self.dir = self.path.parent

    # Fichiers propres au pont, à côté de la configuration
    @property
    def state_path(self):
        return self.dir / "state.json"

    @property
    def plan_path(self):
        return self.dir / "plan.json"

    @property
    def lock_path(self):
        return self.dir / "sync.lock"

    @property
    def log_path(self):
        return self.dir / "logs" / "sync.log"

    @property
    def site(self):
        return self.data["site"].rstrip("/")

    @property
    def index_path(self):
        rel = self.data["index"].get("path")
        return self.resolve(rel) if rel else None

    @property
    def vault_dir(self):
        rel = self.data["vault"].get("dir")
        return self.resolve(rel) if rel else None

    @property
    def texts_enabled(self):
        return bool(self.data["texts"].get("enabled"))

    @property
    def texts_dir(self):
        rel = self.data["texts"].get("dir")
        return self.resolve(rel) if rel and self.texts_enabled else None

    @property
    def interval_minutes(self):
        return int(self.data["schedule"].get("interval_minutes") or 120)

    def resolve(self, rel):
        path = Path(rel).expanduser()
        return path if path.is_absolute() else (self.env_root / path)

    def _finish(self, conf):
        conf.setdefault("mode", "classify")
        conf.setdefault("code", conf.get("dest", "").rstrip("/").split("/")[-1])
        conf["root"] = self.resolve(conf["dest"])
        conf["search_root"] = self.resolve(conf.get("search_root") or conf["dest"])
        return conf

    def course(self, courseid):
        """Configuration d'un cours (dict enrichi) ou None s'il n'est pas configuré."""
        conf = self.data["courses"].get(str(courseid))
        if not conf or conf.get("skip"):
            return None
        conf = {k: v for k, v in conf.items() if k != "routes"}
        return self._finish(conf)

    def conf_for(self, item):
        """Configuration applicable à un fichier : celle du cours, ou d'une route si sa section en relève.

        Une route envoie certaines sections d'un cours Moodle vers une autre destination locale
        (une matière distincte hébergée dans le même cours Moodle, un sous-dossier…).
        """
        base = self.data["courses"].get(str(item["courseid"]))
        if not base or base.get("skip"):
            return None
        for route in base.get("routes", []):
            if re.search(route["sections"], item.get("section") or ""):
                merged = {k: v for k, v in base.items() if k not in ("routes", "search_root", "code")}
                merged.update({k: v for k, v in route.items() if k != "sections"})
                return self._finish(merged)
        return self.course(item["courseid"])

    @property
    def zotero_enabled(self):
        return bool(self.data["zotero"].get("enabled"))

    @property
    def zotero_token(self):
        return self.data["zotero"].get("token") or "@zotero"

    @property
    def zotero_staging(self):
        return self.resolve(self.data["zotero"]["staging"])

    @property
    def zotero_roots(self):
        return [self.resolve(p) for p in self.data["zotero"].get("search_roots", [])]

    def is_skipped(self, courseid):
        conf = self.data["courses"].get(str(courseid))
        return bool(conf and conf.get("skip"))


def make_slug(env_root):
    base = re.sub(r"[^A-Za-z0-9]+", "-", env_root.name).strip("-").lower() or "env"
    return f"{base}-{hashlib.sha1(str(env_root).encode()).hexdigest()[:8]}"


def load(path):
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ConfigError(f"configuration introuvable : {path}")
    try:
        raw = json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        raise ConfigError(f"configuration illisible ({path}) : {exc}") from exc
    data = deep_merge(DEFAULTS, raw)
    if not data.get("site"):
        raise ConfigError("la configuration doit indiquer « site »")
    # macOS peut renvoyer un même chemin accentué en Unicode composé ou décomposé selon la façon dont
    # il a été obtenu (argument, dossier courant…) : normaliser pour que le cache et la tâche planifiée
    # gardent le même identifiant
    path = Path(unicodedata.normalize("NFC", str(path)))
    env_root = Path(unicodedata.normalize("NFC", str((path.parent / data["env_root"]).expanduser().resolve())))
    slug = make_slug(env_root)
    cache_dir = system.cache_root() / slug
    return Config(data=data, path=path, env_root=env_root, cache_dir=cache_dir, slug=slug)
