#!/usr/bin/env python3
"""moodle-bridge : synchronisation en lecture seule d'un Moodle vers un espace de travail local.

Première installation : `python3 install.py` à la racine du dépôt (ou `moodle_bridge.py init`).

Exemples :
  moodle_bridge.py init [--answers réponses.json]
  moodle_bridge.py check-site --site https://moodle.exemple.fr
  moodle_bridge.py token  --config <env>/.moodle-bridge/config.json [--method password|browser|key]
  moodle_bridge.py sync   --config <env>/.moodle-bridge/config.json [--dry-run]
  moodle_bridge.py status --config <env>/.moodle-bridge/config.json [--json]
  moodle_bridge.py courses --config … [--add-all]
"""

import argparse
import html
import importlib
import json
import re
import sys
import time
import urllib.error
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mb import (api, classify, config, credentials, index, schedule, summaries,  # noqa: E402
                sync, system, texts, util, wizard)


def _optional(name):
    """Module présent seulement dans certaines éditions (branches) du pont."""
    try:
        return importlib.import_module(f"mb.{name}")
    except ImportError:
        return None


menubar, notes, skillgen, snapshot = (_optional(n) for n in ("menubar", "notes", "skillgen", "snapshot"))

# Exemples de l'aide pour les parties présentes dans cette édition
EXTRA_EXAMPLES = [line for module, line in (
    (skillgen, "  moodle_bridge.py skill install --config … --target claude|codex|both\n"),
    (notes, "  moodle_bridge.py notes status --config …\n"),
    (menubar, "  moodle_bridge.py menubar install --config …\n")) if module]

LOGIN_TYPES = {1: "formulaire dans l'appli (identifiant + mot de passe Moodle)",
               2: "navigateur externe (SSO)", 3: "navigateur intégré à l'appli (SSO)"}


def load_config(args):
    try:
        return config.load(args.config)
    except config.ConfigError as exc:
        sys.exit(f"Configuration : {exc}")


def make_client(cfg):
    try:
        return api.Client(cfg.site, credentials.read_token(cfg.data["keychain_service"]))
    except credentials.TokenMissing as exc:
        raise api.TokenError("notoken", str(exc)) from exc


# ---------------------------------------------------------------- commandes

def cmd_check_site(args):
    data = api.public_config(args.site)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"Site : {data.get('sitename')} ({data.get('wwwroot')})")
    print(f"API activée : {'oui' if data.get('enablewebservices') else 'non'} ; "
          f"service de l'appli mobile : {'oui' if data.get('enablemobilewebservice') else 'non'}")
    login = data.get("typeoflogin")
    print(f"Connexion de l'appli : {LOGIN_TYPES.get(login, login)}")
    for provider in data.get("identityproviders") or []:
        print(f"Fournisseur d'identité : {provider.get('name')} — {provider.get('url')}")
    if not (data.get("enablewebservices") and data.get("enablemobilewebservice")):
        print("→ Le pont ne peut pas fonctionner : l'API de l'appli mobile est désactivée sur ce site.")
    elif login in (2, 3):
        print("→ Connexion : `token --method browser` (par le navigateur) ou clé de la page « Clés de sécurité ».")
    else:
        print("→ Connexion : `token --method password` (identifiant et mot de passe Moodle).")
    return 0


METHODS = {"password": "identifiant et mot de passe Moodle", "browser": "par le navigateur",
           "key": "clé de la page « Clés de sécurité »"}


def cmd_token(args):
    """Première connexion ou renouvellement du jeton ; toujours lancée par l'utilisateur dans son terminal."""
    cfg = load_config(args)
    service = cfg.data["keychain_service"]
    if args.forget:
        print("Jeton supprimé." if credentials.delete_token(service) else "Aucun jeton enregistré.")
        return 0
    method = args.method
    if not method:
        try:
            method = wizard.LOGIN_HINTS.get(api.public_config(cfg.site).get("typeoflogin"), "browser")
        except (urllib.error.URLError, OSError, ValueError, api.MoodleError):
            method = "browser"
    print(f"Connexion à {cfg.site} — {METHODS[method]} (autre méthode : --method {'|'.join(METHODS)})\n")
    if not wizard.obtain_token(cfg, method, wizard.Prompter()):
        return 1
    who = wizard.check_token(cfg)
    if not who:
        print("Moodle refuse ce jeton : relance `token`, éventuellement avec une autre --method.")
        return 1
    print(f"✓ Connecté à {who[1] or cfg.site} en tant que {who[0]}.")
    return 0


def cmd_init(args):
    """Assistant d'installation (interactif, ou piloté par un fichier de réponses)."""
    answers = None
    if args.answers:
        source = sys.stdin if args.answers == "-" else open(args.answers, encoding="utf-8")
        with source:
            answers = json.load(source)
    try:
        done = wizard.run(answers, actions=install_actions())
    except wizard.Abort as exc:
        if answers is not None:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"\n{exc}")
        return 1
    if answers is not None:
        print(json.dumps(done, ensure_ascii=False, indent=2))
    return 0


def install_actions():
    """Étapes finales de l'assistant, branchées sur les commandes du pont."""
    def run_sync(cfg):
        cmd_sync(argparse.Namespace(config=str(cfg.path), dry_run=False, no_classify=False, quiet=False,
                                    scheduled=False))

    def run_schedule(cfg):
        target, interval = schedule.install(cfg)
        print(f"  ✓ Tâche planifiée ({system.name()}) : toutes les {interval} min — {target}")

    actions = {"sync": run_sync, "schedule": run_schedule}
    if menubar and system.IS_MAC:
        def run_menubar(cfg):
            try:
                print(f"  ✓ Application installée : {menubar.install()}")
            except menubar.BuildError as exc:
                print(f"  ! Application non installée : {exc}")
        actions["menubar"] = run_menubar
    if skillgen:
        def run_skill(cfg, targets):
            for target in targets:
                try:
                    print(f"  ✓ Skill installé pour {target} : {skillgen.install(cfg, skillgen.TARGETS[target])}")
                except skillgen.SkillError as exc:
                    print(f"  ! Skill non installé pour {target} : {exc}")
        actions["skill"] = run_skill
    return actions


def cmd_courses(args):
    cfg = load_config(args)
    client = make_client(cfg)
    info = client.call("core_webservice_get_site_info")
    courses = client.call("core_enrol_get_users_courses", userid=info["userid"])
    if args.add_all:
        mode = args.mode or cfg.data.get("default_mode") or "mirror"
        data = json.loads(cfg.path.read_text("utf-8"))
        before = set(data.get("courses") or {})
        data["courses"] = wizard.courses_config(courses, mode, data.get("courses"))
        util.write_json_atomic(cfg.path, data)
        added = [data["courses"][k] for k in data["courses"] if k not in before]
        print(f"{len(added)} cours ajouté(s) en mode {mode}" + "".join(f"\n  + {c['code']} — {c['name']}" for c in added))
        cfg = load_config(args)
    if args.json:
        print(json.dumps([{"id": c["id"], "shortname": c.get("shortname"), "fullname": util.nfc(c.get("fullname") or ""),
                           "config": cfg.data["courses"].get(str(c["id"]))} for c in courses],
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{info.get('fullname')} — {len(courses)} cours\n")
    for course in sorted(courses, key=lambda c: c.get("fullname", "")):
        conf = cfg.course(course["id"])
        where = (f"→ {conf['dest']} ({conf['mode']})" if conf
                 else "→ ignoré" if cfg.is_skipped(course["id"]) else "→ non configuré")
        print(f"  {course['id']:>8}  {course.get('shortname', ''):<18} {util.nfc(course.get('fullname', ''))[:55]:<55} {where}")
    return 0


def print_plan(cfg, report, log):
    decisions = report["decisions"]
    by_course = {}
    for key, decision in decisions.items():
        by_course.setdefault(key.split("/")[0], []).append((key, decision))
    labels = {"copy": "copier", "present": "présent", "metadata": "inchangé", "zotero": "→ Zotero",
              "in_zotero": "ds Zotero"}
    for courseid, rows in sorted(by_course.items()):
        conf = cfg.course(int(courseid)) or {"code": courseid}
        log(f"\n== {conf['code']} ({len(rows)}) ==")
        for key, decision in sorted(rows, key=lambda r: (r[1].get("dest_dir") or "", r[0])):
            filename = key.rsplit("/", 1)[-1]
            action = labels.get(decision["action"], decision["action"])
            if decision.get("dest_dir") == cfg.data["classify"]["inbox"]:
                action = "à classer"
            how = decision.get("decided_by") or ""
            if decision.get("confidence") is not None:
                how += f" {decision['confidence']:.2f}"
            if decision.get("reason"):
                how += f" — {decision['reason']}"
            log(f"  {action:<9} {str(decision.get('dest_dir') or ''):<28} {filename[:48]:<48} ({how})")


def summary_line(report):
    parts = [f"{len(report['new'])} nouveau(x)", f"{len(report['updated'])} mis à jour",
             f"{len(report['inbox'])} à classer", f"{len(report['zotero'])} à importer dans Zotero",
             f"{len(report['present'])} déjà présent(s)"]
    if report["imported"]:
        parts.append(f"{len(report['imported'])} importé(s) dans Zotero depuis le dernier passage")
    if report["removed"]:
        parts.append(f"{len(report['removed'])} retiré(s) de Moodle")
    if report.get("texts_changed"):
        parts.append(f"{len(report['texts_changed'])} texte(s) modifié(s) sur Moodle")
    if report["errors"]:
        parts.append(f"{len(report['errors'])} erreur(s)")
    return ", ".join(parts)


def notify_report(cfg, report):
    fresh = report["new"] + report["updated"] + report["inbox"] + report["zotero"]
    if fresh:
        codes = Counter(key.split("/")[0] for key in fresh)
        names = ", ".join(f"{(cfg.course(int(c)) or {'code': c})['code']} ({n})" for c, n in codes.most_common())
        extra = "".join([f" — {len(report['inbox'])} à classer" if report["inbox"] else "",
                         f" — {len(report['zotero'])} pour Zotero" if report["zotero"] else ""])
        util.notify("Moodle", f"{len(fresh)} nouveau(x) fichier(s) : {names}{extra}")
    if report["unconfigured"]:
        util.notify("Moodle", f"Cours non configuré(s) : {', '.join(report['unconfigured'].values())}")
    # Un texte de modalités de contrôle modifié change le calcul des notes : toujours le signaler
    state_texts = sync.load_state(cfg.state_path)["remote"].get("texts") or {}
    mcc = [state_texts[t] for t in report.get("texts_changed", [])
           if t in state_texts and texts.is_mcc(state_texts[t], cfg.data["texts"])]
    if mcc:
        codes = sorted({(cfg.course(t["courseid"]) or {"code": t["course"]})["code"] for t in mcc})
        util.notify("Moodle", f"Modalités de contrôle modifiées : {', '.join(codes)}")


def publish(cfg):
    """Index, textes des cours et état de la barre des menus : à jour à chaque appel, pour refléter l'état réel."""
    state = sync.load_state(cfg.state_path)
    index.write(cfg, state)
    texts.write_files(cfg, state)
    if snapshot:
        snapshot.write(cfg, state)


def describe_http_error(exc):
    """« HTTP 403 Forbidden — « Accès interdit… » » : le titre de la page dit souvent pourquoi."""
    title = ""
    try:
        match = re.search(r"<title>(.*?)</title>", exc.read(50_000).decode("utf-8", "replace"), re.S | re.I)
        if match:
            title = f" — « {html.unescape(' '.join(match.group(1).split()))} »"
    except (OSError, ValueError, AttributeError):
        pass
    return f"Moodle inaccessible : HTTP {exc.code} {exc.reason}{title}"


def cmd_sync(args):
    cfg = load_config(args)
    log = util.Logger(cfg.log_path, quiet=args.quiet)
    can_notify = cfg.data.get("notify") and not args.dry_run
    log(f"--- synchronisation {'(simulation) ' if args.dry_run else ''}{util.iso_now()}", screen=not args.quiet)
    try:
        client = make_client(cfg)
        classifier = None if args.no_classify else classify.make_classifier(cfg.data["classify"])
        with sync.Lock(cfg.lock_path):
            if not args.dry_run and snapshot:
                # Tout passage lancé par la tâche planifiée, y compris une relance, recale son minuteur
                snapshot.write(cfg, sync.load_state(cfg.state_path), running=True, scheduled=args.scheduled)
            report = sync.run(cfg, client, classifier, dry_run=args.dry_run, log=log,
                              summarizer=None if args.no_classify else summaries.engine(cfg))
            if not args.dry_run:
                publish(cfg)
    except sync.AlreadyRunning as exc:
        log(str(exc))
        return 0
    except api.TokenError as exc:
        message = f"jeton refusé ({exc.errorcode}) : relancer la commande `token`"
        return _fail(cfg, log, message, can_notify, urgent=True, kind="token")
    except urllib.error.HTTPError as exc:
        return _fail(cfg, log, describe_http_error(exc), can_notify, urgent=False, kind="network")
    except (urllib.error.URLError, TimeoutError, ConnectionError, api.MoodleError, OSError) as exc:
        return _fail(cfg, log, f"échec du passage : {exc}", can_notify, urgent=False, kind="network")
    except Exception as exc:  # noqa: BLE001 — erreur imprévue : la noter et garder l'index à jour quand même
        import traceback
        log(traceback.format_exc())
        return _fail(cfg, log, f"erreur inattendue du pont : {exc!r}", can_notify, urgent=True, kind="bug")

    if args.dry_run:
        print_plan(cfg, report, log)
        log(f"\nSimulation : {len(report['decisions'])} décision(s) enregistrée(s) dans {cfg.plan_path}")
        return 0
    for key, error in report["errors"]:
        log(f"  ! {key} : {error}")
    if report["classifier_error"]:
        log(f"  ! classement automatique : {report['classifier_error']}")
    log(f"Terminé : {summary_line(report)}.")
    if cfg.index_path:
        log(f"Index : {cfg.index_path}")
    if can_notify:
        notify_report(cfg, report)
    return 0


def _fail(cfg, log, message, can_notify, urgent, kind=None):
    log(f"ÉCHEC : {message}")
    state = sync.load_state(cfg.state_path)
    last_ok = util.parse_iso(state.get("last_sync"))
    sync.record_failure(cfg, message, kind)
    try:
        publish(cfg)
    except OSError:
        pass
    # Les coupures réseau au réveil sont fréquentes : ne prévenir qu'après 24 h sans succès
    if can_notify and (urgent or not last_ok or time.time() - last_ok > 86400):
        util.notify("Moodle", message[:200])
    return 2 if urgent else 1


def describe_failure(exc):
    """(message, nature) d'un échec de relevé ; la nature guide l'application (jeton, réseau, bogue)."""
    if isinstance(exc, api.TokenError):
        return f"jeton refusé ({exc.errorcode}) : relancer la commande `token`", "token"
    if isinstance(exc, urllib.error.HTTPError):
        return describe_http_error(exc), "network"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, api.MoodleError, OSError)):
        return f"échec du relevé : {exc}", "network"
    return f"erreur inattendue du pont : {exc!r}", "bug"


def cmd_notes(args):
    """Notes : relevé déterministe (tâche planifiée à part), état, éléments suivis, installation d'openpyxl."""
    cfg = load_config(args)
    if args.action == "setup":
        notes.setup()
        return 0
    if args.action == "schedule":
        return notes_schedule(cfg, args)
    if args.action == "status":
        return notes_status(cfg, args)
    if args.action == "items":
        return notes_items(cfg, args)
    log = util.Logger(notes.log_path(cfg), quiet=args.quiet)
    if not cfg.data["notes"].get("enabled"):
        log("Notes désactivées (« notes.enabled » dans la configuration).")
        return 0
    log(f"--- notes {util.iso_now()}", screen=not args.quiet)
    try:
        report = notes.run(cfg, lambda: make_client(cfg), log,
                           engine=None if args.no_mystery else notes.mystery_engine(cfg),
                           notify=bool(cfg.data.get("notify")) and not args.no_notify, describe=describe_failure)
    except sync.AlreadyRunning as exc:
        log(str(exc))
        return 0
    except Exception as exc:  # noqa: BLE001 — déjà noté dans l'état et la vue par notes.run
        message, kind = describe_failure(exc)
        if kind == "bug":
            import traceback
            log(traceback.format_exc())
        log(f"ÉCHEC : {message}")
        return 2 if kind in ("token", "bug") else 1
    for error in report["errors"]:
        log(f"  ! {error}")
    if report["model_error"]:
        log(f"  ! mystère : {report['model_error']}")
    log(f"Terminé : {report['tracked']} élément(s) de note suivis dans {report['checked']} cours, "
        f"{len(report['new'])} nouvelle(s) note(s), {len(report['revealed'])} découverte(s).")
    return 0


NOTE_STATUS = {"none": "pas de note", "known": "note connue avant l'installation", "mystery": "mystère à découvrir",
               "revealed": "découverte"}


def notes_status(cfg, args):
    state = notes.load_state(cfg)
    view = notes.build_view(cfg, state)
    notes.write_view(cfg, state)
    sched = schedule.status(cfg, "notes")
    if args.json:
        print(json.dumps({**view, "schedule": sched, "workbook_path": str(notes.workbook_path(cfg) or ""),
                          "workbook_python": str(notes.workbook_python(cfg)),
                          "openpyxl": notes.workbook_python(cfg).exists()}, ensure_ascii=False, indent=2))
        return 0
    if not view["enabled"]:
        print("Notes désactivées (« notes.enabled » dans la configuration).")
    last = view["last_check"]
    print(f"Dernier relevé des notes : {util.fmt_date(last, with_time=True) if last else 'jamais'}"
          + (f" — échec : {view['last_error']}" if view["last_error"] else ""))
    print(f"Éléments de note suivis : {view['tracked']}")
    # Une note mystère reste mystère ici aussi : seule l'application la révèle
    for row in view["grades"]:
        if row["status"] == "mystery":
            print(f"  {row['emoji']} {row['code']} · {row['name']} — à découvrir dans la barre des menus")
        else:
            print(f"  {row['code']} · {row['name']} : {row['grade']}" + (f" — {row['workbook']}" if row.get("workbook") else ""))
    print(f"Tâche planifiée : toutes les {sched['interval_minutes']} min ({'active' if sched['loaded'] else 'non chargée'})"
          if sched["installed"] else "Tâche planifiée : non installée")
    path = notes.workbook_path(cfg)
    print(f"Classeur : {path or 'aucun report'}" + ("" if notes.workbook_python(cfg).exists() or not path
                                                     else " — openpyxl absent : lancer `notes setup`"))
    return 0


def notes_items(cfg, args):
    """Éléments de note suivis et leur case du classeur, sans aucune valeur : pour compléter `notes.workbook.map`."""
    state = notes.load_state(cfg)
    rows = []
    for key, entry in sorted(state["items"].items(), key=lambda kv: (kv[1].get("code") or "", kv[1].get("name") or "")):
        target = notes.target_of(cfg, key)
        rows.append({"key": key, "code": entry.get("code"), "name": entry.get("name"), "module": entry.get("module"),
                     "status": entry.get("status"),
                     "workbook": {"element": target[0], "note": target[1]} if target else None})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Aucun élément de note relevé (lancer `notes check`).")
    for row in rows:
        where = f"→ {row['workbook']['element']} Note {row['workbook']['note']}" if row["workbook"] else "→ non relié"
        print(f"{row['key']:<16} {row['code'] or '?':<6} {row['name'][:48]:<48} {NOTE_STATUS.get(row['status'], row['status']):<34} {where}")
    return 0


def notes_schedule(cfg, args):
    action = args.target or "status"
    if action == "install":
        path, interval = schedule.install(cfg, args.interval, job="notes")
        print(f"Relevé des notes planifié : toutes les {interval} min ({path})")
    elif action == "uninstall":
        print("Relevé des notes déplanifié." if schedule.uninstall(cfg, job="notes") else "Aucune tâche de notes installée.")
    elif action == "kick":
        schedule.kick(cfg, job="notes")
        print("Relevé des notes lancé immédiatement (voir le journal).")
    else:
        print(json.dumps(schedule.status(cfg, job="notes"), ensure_ascii=False, indent=2))
    return 0


def cmd_index(args):
    cfg = load_config(args)
    publish(cfg)
    print(f"Index régénéré : {cfg.index_path}" if cfg.index_path else "Aucun index configuré.")
    return 0


def cmd_texts(args):
    """Textes de Moodle conservés dans l'état (aucun appel à Moodle) : tous, un cours, ou les modalités de contrôle."""
    cfg = load_config(args)
    remote = sync.load_state(cfg.state_path)["remote"]
    settings = cfg.data["texts"]
    rows = []
    for record in texts.ordered(remote.get("texts") or {}, remote.get("courses", [])):
        code = (cfg.course(record["courseid"]) or {"code": record.get("course")})["code"]
        if args.course and code.lower() != args.course.lower():
            continue
        mcc = texts.is_mcc(record, settings)
        if (args.mcc and not mcc) or (args.kind and record["kind"] != args.kind):
            continue
        rows.append({**record, "code": code, "mcc": mcc})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Aucun texte correspondant (lancer `sync` si les textes n'ont jamais été relevés).")
    for row in rows:
        print(f"## {row['code']} — {texts.heading(row)} ({texts.KIND_LABELS.get(row['kind'], row['kind'])})")
        print(f"{row.get('url') or ''} · {texts.date_text(row)}\n")
        print(row["text"] + "\n")
    return 0


SESSION_RE = re.compile(r"\b(CM|TD|TP)[\s_-]*0*(\d{1,2})\b", re.IGNORECASE)


def cmd_zotero_pending(args):
    """Supports de cours mis de côté, avec les éléments utiles pour préparer leur import dans Zotero."""
    cfg = load_config(args)
    state = sync.load_state(cfg.state_path)
    rows = []
    for key, entry in sorted(state["items"].items(), key=lambda kv: (kv[1].get("code") or "", kv[1]["filename"])):
        if entry.get("status") != "zotero_pending":
            continue
        code = entry.get("code") or (cfg.course(entry["courseid"]) or {}).get("code")
        marker = SESSION_RE.search(f"{entry['filename']} {entry['module']}")
        rows.append({
            "key": key, "code": code, "course": entry["course"], "section": entry["section"],
            "module": entry["module"], "filename": entry["filename"],
            "path": str(cfg.resolve(entry["local_path"])), "sha256": entry.get("sha256"),
            "moodle_url": entry.get("module_url"), "date": util.fmt_date(entry.get("timemodified")),
            # Indices seulement : l'agent vérifie le contenu avant de choisir titre, collection et tags
            "suggested_tags": ["cours"] + ([code] if code else []) +
                              ([f"{marker.group(1).upper()}{int(marker.group(2))}"] if marker else []),
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("Aucun support de cours en attente d'import dans Zotero.")
    for row in rows:
        print(f"{row['code']:<8} {row['filename'][:60]:<60} {row['section'][:30]:<30} {row['path']}")
    return 0


def cmd_status(args):
    cfg = load_config(args)
    state = sync.load_state(cfg.state_path)
    now = time.time()
    last = util.parse_iso(state.get("last_sync"))
    late = last is None or now - last > cfg.interval_minutes * 60 * 1.25
    counts = Counter(e.get("status") for e in state["items"].values())
    inbox = [e for e in state["items"].values() if e.get("status") == "inbox"]
    state_texts = state["remote"].get("texts") or {}
    info = {
        "last_sync": state.get("last_sync"), "minutes_since": round((now - last) / 60) if last else None,
        "late": late, "interval_minutes": cfg.interval_minutes, "last_error": state.get("last_error"),
        "counts": dict(counts), "inbox": [e.get("local_path") for e in inbox],
        "plan_pending": cfg.plan_path.exists(), "schedule": schedule.status(cfg),
        "index": str(cfg.index_path) if cfg.index_path else None,
        "texts": len(state_texts), "texts_dir": str(cfg.texts_dir) if cfg.texts_dir else None,
        "mcc_texts": sum(1 for t in state_texts.values() if texts.is_mcc(t, cfg.data["texts"])),
    }
    if args.check_token:
        try:
            make_client(cfg).call("core_webservice_get_site_info")
            info["token"] = "valide"
        except api.TokenError as exc:
            info["token"] = f"refusé ({exc.errorcode})"
        except (urllib.error.URLError, OSError) as exc:
            info["token"] = f"non vérifié (réseau : {exc})"
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    if last:
        verdict = "EN RETARD → lancer `sync`" if late else "à jour"
        print(f"Dernière synchronisation réussie : {util.fmt_date(last, with_time=True)} "
              f"(il y a {util.fmt_ago(now - last)}) — {verdict}")
    else:
        print("Aucune synchronisation réussie pour l'instant → lancer `sync`")
    if info["last_error"]:
        print(f"Dernier échec : {info['last_error']}")
    if info.get("token"):
        print(f"Jeton : {info['token']}")
    labels = {"placed": "rangés", "present": "déjà présents", "updated": "mis à jour", "inbox": "à classer",
              "zotero_pending": "à importer dans Zotero", "in_zotero": "dans Zotero",
              "listed": "listés sans téléchargement", "removed": "retirés de Moodle",
              "missing": "supprimés localement", "error": "en erreur"}
    print("Fichiers : " + (", ".join(f"{counts[k]} {v}" for k, v in labels.items() if counts.get(k)) or "aucun"))
    for path in info["inbox"]:
        print(f"  à classer : {path}")
    if info["texts"]:
        print(f"Textes Moodle : {info['texts']} relevés, dont {info['mcc_texts']} de modalités de contrôle"
              + (f" ({info['texts_dir']})" if info["texts_dir"] else ""))
    if info["plan_pending"]:
        print(f"Simulation en attente de validation : {cfg.plan_path}")
    sched = info["schedule"]
    if sched["installed"]:
        print(f"Tâche planifiée : toutes les {sched['interval_minutes']} min "
              f"({'active' if sched['loaded'] else 'non chargée'}, dernier code de sortie : {sched.get('last_exit')})")
    else:
        print("Tâche planifiée : non installée")
    return 0


def cmd_schedule(args):
    cfg = load_config(args)
    if args.action == "install":
        path, interval = schedule.install(cfg, args.interval)
        print(f"Tâche planifiée installée : toutes les {interval} min ({path})")
    elif args.action == "uninstall":
        print("Tâche planifiée supprimée." if schedule.uninstall(cfg) else "Aucune tâche planifiée installée.")
    elif args.action == "kick":
        schedule.kick(cfg)
        print("Synchronisation planifiée lancée immédiatement (voir le journal).")
    else:
        print(json.dumps(schedule.status(cfg), ensure_ascii=False, indent=2))
    return 0


def cmd_menubar(args):
    """Application de barre des menus : commune à tous les environnements du Mac."""
    if args.action == "uninstall":
        print("Application supprimée." if menubar.uninstall(args.dest) else "Application non installée.")
        return 0
    if args.config:
        publish(load_config(args))  # pour que l'application ait tout de suite quelque chose à afficher
    try:
        app = menubar.install(args.dest, launch=not args.no_open)
    except menubar.BuildError as exc:
        sys.exit(f"Compilation impossible : {exc}")
    print(f"Application installée : {app}")
    return 0


def cmd_skill(args):
    """Skill pour agents IA, adapté à cette installation (chemins, commandes, parties présentes)."""
    cfg = load_config(args)
    dests = [Path(args.dest)] if args.dest else [skillgen.TARGETS[t] for t in
                                                 (("claude", "codex") if args.target == "both" else (args.target,))]
    if args.action == "show":
        print(skillgen.render(skillgen.TEMPLATE.read_text("utf-8"), skillgen.flags(cfg), skillgen.values(cfg)))
        return 0
    status = 0
    for dest in dests:
        try:
            print(f"Skill installé : {skillgen.install(cfg, dest)}")
        except skillgen.SkillError as exc:
            print(f"Skill non installé : {exc}")
            status = 1
    return status


def cmd_uninstall(args):
    """Retire ce que l'installation a ajouté au système ; les fichiers rangés et l'index restent en place."""
    cfg = load_config(args)
    jobs = ["sync"] + (["notes"] if notes else [])
    for job in jobs:
        if schedule.uninstall(cfg, job=job):
            print(f"Tâche planifiée « {job} » supprimée.")
    if not args.keep_token and credentials.delete_token(cfg.data["keychain_service"]):
        print("Jeton supprimé.")
    print(f"Conservés : tes fichiers ({cfg.env_root}) et les réglages ({cfg.dir}), à supprimer à la main si besoin.")
    if menubar and system.IS_MAC:
        print("Application de barre des menus : `menubar uninstall` (elle sert à tous les environnements du Mac).")
    if skillgen:
        print("Skill des agents : supprimer le dossier moodle-bridge de ~/.claude/skills ou ~/.codex/skills.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="moodle_bridge.py", description=__doc__ + "".join(EXTRA_EXAMPLES),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="assistant d'installation (première fois, ou mise à jour d'une installation)")
    p.add_argument("--answers", help="réponses en JSON (fichier, ou - pour l'entrée standard) : mode non interactif")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("check-site", help="vérifier qu'un Moodle expose l'API de l'appli mobile")
    p.add_argument("--site", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check_site)

    def with_config(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", required=True, help="chemin de .moodle-bridge/config.json")
        p.set_defaults(func=func)
        return p

    p = with_config("token", cmd_token, "se connecter à Moodle et enregistrer le jeton (interactif)")
    p.add_argument("--method", choices=sorted(METHODS), help="défaut : selon le type de connexion du site")
    p.add_argument("--forget", action="store_true", help="supprimer le jeton enregistré")
    p = with_config("courses", cmd_courses, "lister les cours et leur destination")
    p.add_argument("--add-all", action="store_true", help="suivre tous les cours pas encore configurés")
    p.add_argument("--mode", choices=["mirror", "classify"], help="mode des cours ajoutés (défaut : default_mode)")
    p.add_argument("--json", action="store_true")
    p = with_config("sync", cmd_sync, "synchroniser maintenant")
    p.add_argument("--dry-run", action="store_true", help="simuler : télécharge dans le cache et écrit plan.json")
    p.add_argument("--no-classify", action="store_true", help="ne pas appeler le modèle de langage")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--scheduled", action="store_true", help="lancé par la tâche planifiée")
    with_config("index", cmd_index, "régénérer l'index (et les textes des cours) à partir de l'état")
    p = with_config("texts", cmd_texts, "afficher les textes Moodle conservés (sections, pages, consignes, annonces)")
    p.add_argument("--course", help="code du cours (nom de son dossier, ex. ABC)")
    p.add_argument("--mcc", action="store_true", help="seulement les modalités de contrôle")
    p.add_argument("--kind", choices=sorted(texts.KIND_LABELS), help="type de texte")
    p.add_argument("--json", action="store_true")
    p = with_config("zotero-pending", cmd_zotero_pending, "supports de cours en attente d'import dans Zotero")
    p.add_argument("--json", action="store_true")
    p = with_config("status", cmd_status, "état de la synchronisation (retard, à classer, tâche planifiée)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--check-token", action="store_true")
    p = with_config("schedule", cmd_schedule, "gérer la tâche planifiée (launchd, Planificateur de tâches, cron)")
    p.add_argument("action", choices=["install", "uninstall", "status", "kick"])
    p.add_argument("--interval", type=int, help="minutes entre deux passages")
    p = with_config("uninstall", cmd_uninstall, "retirer tâches planifiées et jeton (les fichiers restent)")
    p.add_argument("--keep-token", action="store_true")
    if notes:
        p = with_config("notes", cmd_notes, "notes Moodle : relevé fréquent, notification mystère, report dans le classeur")
        p.add_argument("action", choices=["check", "status", "items", "setup", "schedule"])
        p.add_argument("target", nargs="?", choices=["install", "uninstall", "status", "kick"],
                       help="pour `schedule` : action sur la tâche planifiée des notes")
        p.add_argument("--interval", type=int, help="minutes entre deux relevés (défaut : notes.interval_minutes)")
        p.add_argument("--json", action="store_true")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--scheduled", action="store_true", help="lancé par la tâche planifiée")
        p.add_argument("--no-notify", action="store_true", help="ne pas envoyer de notification")
        p.add_argument("--no-mystery", action="store_true", help="ne pas appeler le modèle (mystère par défaut 🎁)")
    if skillgen:
        p = with_config("skill", cmd_skill, "installer le skill des agents IA (Claude Code, Codex…)")
        p.add_argument("action", choices=["install", "show"])
        p.add_argument("--target", choices=["claude", "codex", "both"], default="both")
        p.add_argument("--dest", help="dossier de skills d'un autre agent (remplace --target)")
    if menubar:
        p = sub.add_parser("menubar", help="installer l'application de barre des menus macOS (état du pont)")
        p.add_argument("action", choices=["install", "uninstall"])
        p.add_argument("--config", help="environnement à afficher tout de suite (facultatif)")
        p.add_argument("--dest", default=str(menubar.DEFAULT_DEST), help="dossier d'installation")
        p.add_argument("--no-open", action="store_true", help="ne pas lancer l'application après l'installation")
        p.set_defaults(func=cmd_menubar)

    args = parser.parse_args(argv)
    result = args.func(args)
    # L'index et l'état de l'application doivent refléter l'état à chaque appel
    if args.command in ("status", "zotero-pending", "courses"):
        try:
            publish(config.load(args.config))
        except (OSError, ValueError, config.ConfigError) as exc:
            print(f"(index non régénéré : {exc})", file=sys.stderr)
    return result


if __name__ == "__main__":
    sys.exit(main())
