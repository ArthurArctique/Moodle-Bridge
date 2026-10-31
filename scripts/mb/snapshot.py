"""État résumé du pont pour l'application de barre des menus, réécrit à chaque appel comme l'index.

Il est rangé dans le cache du pont (~/Library/Caches/moodle-bridge/<slug>/status.json), hors de l'espace de
travail : l'application le lit sans demander l'accès à un dossier OneDrive ou iCloud, et trouve d'elle-même
tous les environnements installés sur le Mac.
"""

import json
import os
import sys
import time
from pathlib import Path

from . import schedule, summaries, util, vault

try:
    from . import ai
except ImportError:  # édition sans IA
    ai = None
from .index import INITIAL_IMPORT_THRESHOLD, entry_code, recent_announcements, upcoming

VERSION = 1
FILENAME = "status.json"
MAX_RECENT = 15
MAX_ANNOUNCEMENTS = summaries.MAX_ANNOUNCEMENTS
MAX_RUNS = 12
RECENT_STATUSES = ("placed", "updated", "inbox", "zotero_pending", "in_zotero")


def path(cfg):
    return cfg.cache_dir / FILENAME


def _ts(iso):
    return int(util.parse_iso(iso)) if iso else None


def _file(cfg, key, entry):
    local = entry.get("local_path")
    return {
        "key": key, "code": entry_code(cfg, entry), "filename": entry["filename"],
        "status": entry.get("status"), "placed_at": entry.get("placed_at"),
        "path": str(cfg.resolve(local)) if local else None,
        "folder": Path(local).parent.as_posix() if local else None,
        "moodle_url": entry.get("module_url"),
    }


def recent_files(cfg, items):
    """Derniers fichiers arrivés de Moodle, sans le lot de l'import initial."""
    batches = {}
    for entry in items.values():
        if entry.get("status") in RECENT_STATUSES and entry.get("placed_at"):
            batches[entry["placed_at"]] = batches.get(entry["placed_at"], 0) + 1
    rows = [(key, entry) for key, entry in items.items()
            if entry.get("status") in RECENT_STATUSES and entry.get("placed_at")
            and batches[entry["placed_at"]] <= INITIAL_IMPORT_THRESHOLD]
    rows.sort(key=lambda kv: (-kv[1]["placed_at"], kv[1].get("code") or "", kv[1]["filename"]))
    return [_file(cfg, key, entry) for key, entry in rows[:MAX_RECENT]]


def _run(entry):
    run = {"at": _ts(entry.get("at")), "ok": "error" not in entry}
    if run["ok"]:
        run.update({k: entry.get(k, 0) for k in ("new", "updated", "inbox", "zotero", "imported", "errors", "removed")})
    else:
        run.update({"error": entry["error"], "kind": entry.get("kind")})
    return run


def _announcement(cfg, state, ann):
    texts = state.get("remote", {}).get("texts") or {}
    summary, kind = summaries.for_snapshot(state.get("summaries") or {}, texts, ann)
    return {"at": ann["time"], "code": entry_code(cfg, ann), "name": ann.get("name") or "", "url": ann.get("url"),
            "attachment": bool(ann.get("attachment")), "summary": summary, "summary_kind": kind}


def _argv(cfg, command):
    return [str(Path(sys.executable).resolve()), "-I", str(schedule.entry_script()), command, "--config", str(cfg.path)]


def build(cfg, state, now=None, running=False, scheduled=False, previous=None):
    now = int(now or time.time())
    items = state.get("items", {})
    remote = state.get("remote", {})
    days = int(cfg.data["index"].get("news_days") or 7)
    attempts = [t for t in (_ts(state.get("last_sync")), _ts(state.get("last_attempt"))) if t]

    def waiting(status):
        return [_file(cfg, key, e) for key, e in sorted(items.items(), key=lambda kv: kv[1]["filename"])
                if e.get("status") == status]

    return {
        "version": VERSION, "generated_at": now, "slug": cfg.slug,
        "env_name": cfg.env_root.name, "env_root": str(cfg.env_root), "config": str(cfg.path),
        "site": cfg.site, "sitename": remote.get("sitename"),
        # Ce que « Ouvrir dans Obsidian » ouvre : l'accueil du coffre, sinon l'ancien index
        "index_path": str(cfg.vault_dir / f"{vault.HOME}.md") if cfg.vault_dir else (
            str(cfg.index_path) if cfg.index_path else None),
        "log_path": str(cfg.log_path),
        "launchd_label": schedule.label(cfg), "schedule_installed": schedule.installed(cfg),
        "interval_minutes": cfg.interval_minutes,
        # Pendant un passage : l'application vérifie que ce processus vit encore
        "running": running, "pid": os.getpid() if running else None, "started_at": now if running else None,
        # Le minuteur de launchd repart de chaque passage qu'il lance, relances à la main comprises
        # (vérifié sur macOS 27) : c'est le point de départ de l'estimation du prochain passage
        "last_scheduled_at": now if running and scheduled else (previous or {}).get("last_scheduled_at"),
        "last_sync": _ts(state.get("last_sync")), "last_attempt": max(attempts) if attempts else None,
        "last_error": state.get("last_error"),
        "last_error_kind": state.get("last_error_kind") if state.get("last_error") else None,
        "counts": {
            "tracked": sum(1 for e in items.values() if e.get("status") not in ("removed", "missing")),
            "inbox": sum(1 for e in items.values() if e.get("status") == "inbox"),
            "zotero_pending": sum(1 for e in items.values() if e.get("status") == "zotero_pending"),
        },
        "recent_files": recent_files(cfg, items),
        "inbox": waiting("inbox"),
        "zotero_pending": waiting("zotero_pending"),
        "upcoming": upcoming(cfg, remote, now),
        "announcements": [_announcement(cfg, state, a)
                          for a in recent_announcements(remote, days, now)[:MAX_ANNOUNCEMENTS]],
        "history": [_run(h) for h in reversed(state.get("history", [])[-MAX_RUNS:])],
        "sync_argv": _argv(cfg, "sync") + ["--quiet"],
        "token_argv": _argv(cfg, "token"),
        # Onglet IA : réglage actuel (lu dans la configuration, sans lancer de commande) et commande à appeler
        "vault_dir": str(cfg.vault_dir) if cfg.vault_dir else None,
        "ai": ai.current(cfg) if ai else None,
        "ai_argv": _argv(cfg, "ai") if ai else None,
    }


def read(cfg):
    try:
        return json.loads(path(cfg).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def write(cfg, state, running=False, scheduled=False, now=None):
    util.write_json_atomic(path(cfg), build(cfg, state, now, running, scheduled, previous=read(cfg)))
    return path(cfg)
