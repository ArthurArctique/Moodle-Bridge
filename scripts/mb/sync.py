"""Synchronisation : détecte les nouveautés, télécharge, range sans jamais écraser, met à jour l'état."""

import copy
import hashlib
import os
import shutil
import time
import urllib.error
from pathlib import Path

from . import classify, fetch, summaries, system, texts, util
from .api import MoodleError, TokenError

PLACED = classify.PLACED
TRACKED = PLACED | {"inbox", "zotero_pending"}
META_FIELDS = ("courseid", "course", "cmid", "modname", "module", "module_url", "section",
               "section_num", "filepath", "filename", "filesize", "timemodified", "mimetype",
               "fileurl", "kind")


# ---------------------------------------------------------------- état et verrou

def empty_state():
    return {"version": 1, "items": {}, "hash_cache": {}, "remote": {}, "history": [],
            "last_sync": None, "last_attempt": None, "last_error": None}


def load_state(path):
    import json
    path = Path(path)
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text("utf-8"))
    for key, value in empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(path, state, env_root):
    cache = state.get("hash_cache", {})
    for rel in [r for r in cache if not (Path(env_root) / r).exists()]:
        del cache[rel]
    state["history"] = state.get("history", [])[-50:]
    util.write_json_atomic(path, state)


class AlreadyRunning(Exception):
    pass


class Lock:
    """Empêche deux synchronisations simultanées ; un verrou de plus d'une heure est considéré abandonné."""

    def __init__(self, path, stale_after=3600):
        self.path = Path(path)
        self.stale_after = stale_after

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise AlreadyRunning(f"une synchronisation est déjà en cours ({self.path})")
            with os.fdopen(fd, "w") as handle:
                handle.write(f"{os.getpid()} {int(time.time())}\n")
            return self
        raise AlreadyRunning(f"verrou impossible à prendre ({self.path})")

    def _is_stale(self):
        try:
            pid, started = self.path.read_text().split()[:2]
            if time.time() - int(started) > self.stale_after:
                return True
            return not system.pid_alive(int(pid))
        except (OSError, ValueError):
            return True

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------- fichiers locaux

class LocalFiles:
    """Index paresseux des fichiers locaux par taille, avec empreintes mises en cache dans l'état."""

    def __init__(self, env_root, hash_cache):
        self.env_root = Path(env_root)
        self.cache = hash_cache
        self.by_root = {}

    def rel(self, path):
        try:
            return Path(path).relative_to(self.env_root).as_posix()
        except ValueError:
            return str(path)

    def _index(self, root):
        root = Path(root)
        if root not in self.by_root:
            sizes = {}
            if root.exists():
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for name in filenames:
                        if name.startswith(".") or name.endswith(".part"):
                            continue
                        path = Path(dirpath) / name
                        try:
                            sizes.setdefault(path.stat().st_size, []).append(path)
                        except OSError:
                            continue
            self.by_root[root] = sizes
        return self.by_root[root]

    def sha(self, path):
        info = Path(path).stat()
        rel = self.rel(path)
        cached = self.cache.get(rel)
        if cached and cached[0] == info.st_size and cached[1] == info.st_mtime_ns:
            return cached[2]
        digest = util.sha256_file(path)
        self.cache[rel] = [info.st_size, info.st_mtime_ns, digest]
        return digest

    def find_identical(self, root, size, digest):
        for path in self._index(root).get(size, []):
            try:
                if path.exists() and self.sha(path) == digest:
                    return path
            except OSError:
                continue
        return None

    def add(self, path):
        path = Path(path)
        size = path.stat().st_size
        for root, sizes in self.by_root.items():
            try:
                path.relative_to(root)
            except ValueError:
                continue
            sizes.setdefault(size, []).append(path)


def place(src, target_dir, filename, digest, timemodified, local):
    """Copie src dans target_dir sans jamais écraser. Renvoie (chemin, "copied" | "present")."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = util.safe_name(filename, 200)
    target = target_dir / name
    if target.exists():
        if local.sha(target) == digest:
            return target, "present"
        stem, ext = os.path.splitext(name)
        day = time.strftime("%Y-%m-%d", time.localtime(timemodified or time.time()))
        number = 1
        while True:
            suffix = f" (Moodle {day})" if number == 1 else f" (Moodle {day} {number})"
            candidate = target_dir / f"{stem}{suffix}{ext}"
            if not candidate.exists():
                target = candidate
                break
            if local.sha(candidate) == digest:
                return candidate, "present"
            number += 1
    tmp = target_dir / f".{target.name}.moodle-part"
    shutil.copyfile(src, tmp)
    if timemodified:
        os.utime(tmp, (timemodified, timemodified))
    if target.exists():  # apparu entre-temps : ne jamais écraser
        tmp.unlink()
        raise FileExistsError(f"{target} est apparu pendant la copie")
    os.rename(tmp, target)
    local.cache[local.rel(target)] = [target.stat().st_size, target.stat().st_mtime_ns, digest]
    local.add(target)
    return target, "copied"


# ---------------------------------------------------------------- règles

def disposition(item, cfg):
    """« skip » (page web Moodle), « list » (listé sans téléchargement) ou « fetch »."""
    exclude = cfg.data["exclude"]
    if item["modname"] in exclude["modnames"]:
        return "skip", None
    mimetype = item.get("mimetype") or ""
    if any(mimetype.startswith(prefix) for prefix in exclude["list_only_mimetypes"]):
        return "list", "video" if mimetype.startswith("video/") else "média"
    limit = exclude.get("max_size_mb")
    if limit and item["filesize"] > limit * 1_000_000:
        return "list", "trop lourd"
    return "fetch", None


def cache_file_for(cfg, item):
    tag = f"{item['key']}|{item['timemodified']}|{item['filesize']}"
    return cfg.cache_dir / hashlib.sha1(tag.encode()).hexdigest()


# ---------------------------------------------------------------- synchronisation

def _find_in_zotero(cfg, local, size, digest):
    for root in cfg.zotero_roots if cfg.zotero_enabled else []:
        found = local.find_identical(root, size, digest)
        if found:
            return found
    return None


def run(cfg, client, classifier=None, dry_run=False, log=print, summarizer=None):
    """Synchronise et renvoie un rapport. En simulation, rien n'est écrit hors de plan.json.

    `summarizer` résume les annonces récentes pour l'application de barre des menus (voir `summaries`).
    """
    state = load_state(cfg.state_path)
    items = copy.deepcopy(state["items"]) if dry_run else state["items"]
    hash_cache = copy.deepcopy(state["hash_cache"]) if dry_run else state["hash_cache"]
    local = LocalFiles(cfg.env_root, hash_cache)
    settings = cfg.data["classify"]
    inbox = settings["inbox"]
    token = cfg.zotero_token if cfg.zotero_enabled else None
    now = int(time.time())
    report = {"new": [], "updated": [], "present": [], "inbox": [], "zotero": [], "imported": [],
              "errors": [], "removed": [], "moved": [], "missing": [], "decisions": {},
              "unconfigured": {}, "listed": 0, "classifier_error": None, "texts": 0, "texts_changed": []}

    if not dry_run:
        state["last_attempt"] = util.iso_now()
    snap = fetch.snapshot(client, log=log,
                          announcements_per_course=cfg.data["index"]["announcements_per_course"],
                          with_texts=cfg.texts_enabled)
    remote = snap["items"]
    log(f"Moodle : {len(snap['courses'])} cours, {len(remote)} fichiers distants.")
    for course in snap["courses"]:
        if cfg.course(course["id"]) is None and not cfg.is_skipped(course["id"]):
            report["unconfigured"][course["id"]] = course["shortname"]

    # Textes (sections, pages, consignes, annonces) : seulement pour les cours configurés
    previous = state["remote"].get("texts") or {}
    text_courses = set(state["remote"].get("texts_courses") or [])
    kept_texts, changed = texts.merge(
        previous if cfg.texts_enabled else {}, snap["texts"], lambda cid: cfg.course(cid) is not None,
        {c["id"] for c in snap["courses"]}, text_courses, set(snap["failed_texts"]), now)
    report["texts"] = len(kept_texts)
    report["texts_changed"] = [tid for tid in changed if kept_texts[tid]["kind"] != "announcement"]
    if cfg.texts_enabled:
        log(f"Textes : {len(kept_texts)} relevés, {len(report['texts_changed'])} modifié(s) depuis le dernier passage.")

    # Fichiers retirés de Moodle : conservés localement, simplement marqués
    for key, entry in items.items():
        if key not in remote and entry.get("status") not in ("removed", "listed"):
            entry["status_before_removal"] = entry.get("status")
            entry["status"] = "removed"
            entry["removed_at"] = now
            report["removed"].append(key)

    # Supports mis de côté pour Zotero et désormais importés : retrouvés par empreinte
    for key, entry in items.items():
        if entry.get("status") != "zotero_pending" or not entry.get("sha256"):
            continue
        found = _find_in_zotero(cfg, local, entry.get("filesize") or 0, entry["sha256"])
        if not found:
            continue
        staged = cfg.env_root / entry["local_path"] if entry.get("local_path") else None
        entry.update({"status": "in_zotero", "local_path": local.rel(found), "imported_at": now})
        report["imported"].append(key)
        if staged and not dry_run:
            try:
                staged.relative_to(cfg.zotero_staging)
                staged.unlink(missing_ok=True)  # copie temporaire du pont, plus utile
            except ValueError:
                pass

    # Fichiers déplacés ou supprimés à la main depuis le dernier passage
    for key, entry in items.items():
        if entry.get("status") not in TRACKED or not entry.get("local_path"):
            continue
        if (cfg.env_root / entry["local_path"]).exists():
            continue
        conf = cfg.conf_for(entry)
        size, digest = entry.get("filesize") or 0, entry.get("sha256")
        found = digest and conf and local.find_identical(conf["search_root"], size, digest)
        in_zotero = digest and not found and _find_in_zotero(cfg, local, size, digest)
        if found:
            entry["local_path"] = local.rel(found)
            entry["dest_dir"] = classify.dest_dir_for(conf["root"], found, entry.get("filepath"))
            if entry["status"] in ("inbox", "zotero_pending") and entry["dest_dir"] \
                    and entry["dest_dir"].split("/")[0] != inbox:
                entry["status"], entry["decided_by"] = "placed", "manuel"
            report["moved"].append(key)
        elif in_zotero:
            entry.update({"status": "in_zotero", "local_path": local.rel(in_zotero), "dest_dir": token})
            report["moved"].append(key)
        else:
            entry["status"] = "missing"  # supprimé localement : ne jamais le retélécharger
            report["missing"].append(key)

    # Ce qu'il faut récupérer
    todo = {}
    for key, item in remote.items():
        meta = {field: item[field] for field in META_FIELDS}
        entry = items.get(key)
        if cfg.conf_for(item) is None:
            continue
        action, reason = disposition(item, cfg)
        if action == "skip":
            items.pop(key, None)
            continue
        if action == "list":
            items[key] = {**(entry or {}), **meta, "status": "listed", "list_reason": reason}
            report["listed"] += 1
            continue
        if entry and entry.get("status") == "removed":
            entry["status"] = entry.pop("status_before_removal", None) or "error"
        if entry is None or entry.get("status") in ("listed", "error"):
            todo[key] = "new"
        elif entry.get("status") == "missing":
            entry.update(meta)
        elif (item["timemodified"], item["filesize"]) != (entry.get("timemodified"), entry.get("filesize")):
            todo[key] = "updated"
        else:
            entry.update(meta)

    # Téléchargement dans le cache (hors de l'espace de travail) et empreintes
    downloaded = {}
    for key, why in todo.items():
        item = remote[key]
        cache_file = cache_file_for(cfg, item)
        try:
            if not cache_file.exists():
                client.download(item["fileurl"], cache_file, expected_size=item["filesize"] or None)
            downloaded[key] = (cache_file, util.sha256_file(cache_file))
        except TokenError:
            raise
        except (MoodleError, OSError, urllib.error.URLError) as exc:
            report["errors"].append((key, str(exc)))
            if why == "updated":
                # Métadonnées inchangées : la nouvelle version sera retentée au prochain passage
                items[key]["error"] = str(exc)
            else:
                items[key] = {**{f: item[f] for f in META_FIELDS}, "status": "error", "error": str(exc)}
    if todo:
        log(f"{len(downloaded)} fichier(s) récupéré(s) sur {len(todo)} nouveau(x) ou modifié(s).")

    # Décisions de placement, de la plus sûre à la plus incertaine
    plan = {}
    if not dry_run and cfg.plan_path.exists():
        import json
        plan = json.loads(cfg.plan_path.read_text("utf-8")).get("decisions", {})
    decisions, pending, present_local = {}, {}, {}
    # Clés comparées en Unicode composé : un nom accentué peut arriver décomposé de Moodle
    overrides = {util.nfc(k): v for k, v in cfg.data["overrides"].items()}
    for key, (cache_file, digest) in downloaded.items():
        item, why, entry = remote[key], todo[key], items.get(key) or {}
        conf = cfg.conf_for(item)
        size = cache_file.stat().st_size
        if why == "updated" and entry.get("sha256") == digest:
            decisions[key] = {"action": "metadata"}
            continue
        in_zotero = _find_in_zotero(cfg, local, size, digest)
        if in_zotero:
            decisions[key] = {"action": "in_zotero", "local": in_zotero, "decided_by": "déjà dans Zotero",
                              "dest_dir": token}
            continue
        raw_override = overrides.get(util.nfc(key))
        override = raw_override if token and raw_override == token else classify.normalize_dest(raw_override)
        same = local.find_identical(conf["search_root"], size, digest)
        if same:
            present = {"action": "present", "local": same, "decided_by": "déjà présent",
                       "dest_dir": classify.dest_dir_for(conf["root"], same, item["filepath"])}
            decisions[key] = present
            # Avec Zotero, un support de cours déjà déposé dans l'arborescence doit quand même y être importé :
            # la copie locale reste en place, une copie est mise de côté pour l'import
            if not token or conf["mode"] == "mirror" or (override and override != token):
                continue
            present_local[key] = present
            if override == token:
                decisions[key] = {"action": "copy", "dest_dir": token, "decided_by": "correction"}
            else:
                del decisions[key]
                pending[key] = item
            continue
        kept = plan.get(key, {})
        if override:
            decisions[key] = {"action": "copy", "dest_dir": override, "decided_by": "correction"}
        elif conf["mode"] == "mirror":
            decisions[key] = {"action": "copy", "dest_dir": classify.mirror_dest(item), "decided_by": "miroir"}
        elif kept.get("sha256") == digest and kept.get("dest_dir") and kept.get("root") == str(conf["root"]):
            decisions[key] = {"action": "copy", "dest_dir": kept["dest_dir"], "decided_by": kept.get("decided_by"),
                              "confidence": kept.get("confidence"), "reason": kept.get("reason")}
        elif why == "updated" and entry.get("dest_dir"):
            decisions[key] = {"action": "copy", "dest_dir": entry["dest_dir"], "decided_by": "version précédente"}
        else:
            pending[key] = item

    def evidence():
        known = [e for e in items.values() if e.get("status") in PLACED | {"in_zotero"} and e.get("dest_dir")]
        for key, decision in decisions.items():
            if decision.get("dest_dir") and decision["action"] in ("copy", "present", "in_zotero"):
                known.append({**remote[key], "dest_dir": decision["dest_dir"]})
        return known

    # Voisins : répété tant que de nouveaux placements servent d'exemples
    progress = True
    while progress and pending:
        progress = False
        known = evidence()
        for key in list(pending):
            found = classify.sibling_dest(pending[key], known, inbox)
            if found:
                decisions[key] = {"action": "copy", "dest_dir": found[0], "decided_by": found[1]}
                del pending[key]
                progress = True

    # Modèle de langage, destination par destination ; boîte d'attente pour le reste
    names = {c["id"]: c["fullname"] for c in snap["courses"]}
    groups = {}
    for key, item in pending.items():
        conf = cfg.conf_for(item)
        groups.setdefault(str(conf["root"]), (conf, []))[1].append({**item, "key": key})
    for conf, batch in groups.values():
        accepted, refused = {}, {}
        if classifier:
            examples = [e for e in evidence() if (cfg.conf_for(e) or {}).get("root") == conf["root"]]
            context = classify.context_excerpt(cfg.env_root, settings.get("context_files", []), conf.get("code"))
            try:
                log(f"Classement de {len(batch)} fichier(s) de {conf.get('code')} par le modèle…")
                accepted, refused = classify.classify_course(
                    classifier, conf, names.get(batch[0]["courseid"], ""), batch, examples, settings,
                    context, token)
                if getattr(classifier, "models_used", None):
                    log(f"  modèle utilisé : {', '.join(sorted(classifier.models_used))}")
            except classify.ClassifierError as exc:
                report["classifier_error"] = str(exc)
                log(f"  ! classement automatique indisponible : {exc}")
        for entry in batch:
            key = entry["key"]
            if key in accepted:
                decisions[key] = {"action": "copy", "decided_by": "modèle", **accepted[key]}
            else:
                why = refused.get(key, {}).get("reason") or ("aucun modèle configuré" if not classifier
                                                             else report["classifier_error"] or "sans réponse")
                decisions[key] = {"action": "copy", "dest_dir": inbox, "decided_by": "à classer",
                                  "confidence": refused.get(key, {}).get("confidence"), "reason": why}

    # Supports de cours : mis de côté pour un import dans Zotero, jamais rangés dans l'arborescence
    for decision in decisions.values():
        if token and decision.get("action") == "copy" and decision.get("dest_dir") == token:
            decision["action"] = "zotero"
    # Fichiers déjà présents localement : seule une décision « Zotero » compte, sinon ils restent où ils sont
    for key, present in present_local.items():
        decision = decisions.get(key)
        if decision and decision.get("action") == "zotero":
            decision["also_local"] = present["local"]
        else:
            decisions[key] = present

    report["decisions"] = decisions
    if dry_run:
        _write_plan(cfg, decisions, downloaded, remote, local)
        return report

    # Application
    for key, decision in decisions.items():
        item, why = remote[key], todo[key]
        entry = items.get(key) or {}
        conf = cfg.conf_for(item)
        cache_file, digest = downloaded[key]
        meta = {field: item[field] for field in META_FIELDS}
        if decision["action"] == "metadata":
            items[key] = {**entry, **meta}
        elif decision["action"] in ("present", "in_zotero"):
            status = decision["action"]
            items[key] = {**entry, **meta, "sha256": digest, "status": status,
                          "local_path": local.rel(decision["local"]), "dest_dir": decision.get("dest_dir"),
                          "decided_by": decision["decided_by"], "placed_at": entry.get("placed_at") or now}
            report["present"].append(key)
        else:
            if decision["action"] == "zotero":
                target_dir = cfg.zotero_staging / util.safe_name(conf.get("code") or "cours", 40)
            else:
                target_dir = conf["root"] / decision["dest_dir"] / item["filepath"].strip("/")
            try:
                target, how = place(cache_file, target_dir, item["filename"], digest, item["timemodified"], local)
            except OSError as exc:
                report["errors"].append((key, str(exc)))
                continue
            if how == "present":
                status = "present"
            elif decision["action"] == "zotero":
                status = "zotero_pending"
            elif decision["dest_dir"].split("/")[0] == inbox:
                status = "inbox"
            else:
                status = "updated" if why == "updated" else "placed"
            previous = entry.get("previous_paths", []) + ([entry["local_path"]] if why == "updated" and entry.get("local_path") else [])
            items[key] = {**entry, **meta, "sha256": digest, "status": status, "local_path": local.rel(target),
                          "dest_dir": decision["dest_dir"], "decided_by": decision.get("decided_by"),
                          "confidence": decision.get("confidence"), "reason": decision.get("reason"),
                          "code": conf.get("code"), "placed_at": now, "previous_paths": previous,
                          "local_copy": local.rel(decision["also_local"]) if decision.get("also_local") else None}
            items[key].pop("error", None)
            bucket = {"present": "present", "inbox": "inbox", "updated": "updated",
                      "zotero_pending": "zotero"}.get(status, "new")
            report[bucket].append(key)
        try:
            cache_file.unlink()
        except OSError:
            pass

    state["remote"] = {k: snap[k] for k in ("fetched_at", "user", "sitename", "courses",
                                            "assignments", "events", "announcements")}
    state.setdefault("summaries", {})
    summaries.update(cfg, state["summaries"], state["remote"], kept_texts, summarizer, log, now,
                     limit=summaries.MAX_ANNOUNCEMENTS)
    state["remote"]["unconfigured"] = {str(k): v for k, v in report["unconfigured"].items()}
    state["remote"]["texts"] = kept_texts
    state["remote"]["texts_courses"] = sorted(
        text_courses | {c["id"] for c in snap["courses"] if cfg.course(c["id"])} if cfg.texts_enabled else [])
    state["last_sync"] = util.iso_now()
    state["last_error"] = None
    state["history"].append({"at": state["last_sync"], **{k: len(report[k]) for k in
                             ("new", "updated", "present", "inbox", "zotero", "imported", "errors", "removed",
                              "texts_changed")}})
    save_state(cfg.state_path, state, cfg.env_root)
    if cfg.plan_path.exists():
        cfg.plan_path.unlink()
    return report


def _write_plan(cfg, decisions, downloaded, remote, local):
    plan = {}
    for key, decision in decisions.items():
        item = remote[key]
        conf = cfg.conf_for(item)
        plan[key] = {
            "course": item["course"], "code": conf.get("code"), "root": str(conf["root"]),
            "section": item["section"], "module": item["module"],
            "filename": item["filename"], "action": decision["action"],
            "dest_dir": decision.get("dest_dir"), "decided_by": decision.get("decided_by"),
            "confidence": decision.get("confidence"), "reason": decision.get("reason"),
            "local": local.rel(decision["local"]) if decision.get("local") else None,
            "also_local": local.rel(decision["also_local"]) if decision.get("also_local") else None,
            "sha256": downloaded[key][1],
        }
    util.write_json_atomic(cfg.plan_path, {"created": util.iso_now(), "decisions": plan})


def record_failure(cfg, message, kind=None):
    """Note l'échec du passage dans l'état et l'historique, sans toucher au reste.

    `kind` : « token » (nouveau jeton nécessaire), « network » (Moodle injoignable) ou « bug ».
    """
    state = load_state(cfg.state_path)
    state["last_attempt"] = util.iso_now()
    state["last_error"] = message
    state["last_error_kind"] = kind
    state["history"] = (state.get("history", []) + [{"at": state["last_attempt"], "error": message, "kind": kind}])[-50:]
    util.write_json_atomic(cfg.state_path, state)
