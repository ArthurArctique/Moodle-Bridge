"""Index Markdown (compatible Obsidian) : échéances, nouveautés, modalités de contrôle et contenu de chaque cours."""

import time
from collections import OrderedDict, defaultdict
from pathlib import Path

from . import summaries, texts, util
from .fetch import browser_url

INITIAL_IMPORT_THRESHOLD = 25
MAX_UPCOMING = 15


def esc(text):
    return (text or "").replace("[", "\\[").replace("]", "\\]")


def cell(text):
    return esc(text).replace("|", "\\|")


def file_link(cfg, rel, label):
    return f"[{esc(label)}]({texts.link_from(cfg.index_path, Path(cfg.env_root) / rel)})"


def web_link(url, label):
    return f"[{esc(label)}]({url})" if url else esc(label)


def course_code(cfg, courseid, fallback):
    conf = cfg.course(courseid)
    return conf.get("code") if conf else fallback


def upcoming(cfg, remote, now):
    """Échéances à venir : événements du calendrier, puis dates limites de devoirs absentes du calendrier."""
    rows = []
    seen = set()
    for event in remote.get("events", []):
        if event.get("timestart", 0) < now - 3600:
            continue
        seen.add((event.get("courseid"), event.get("timestart")))
        rows.append({"at": event["timestart"], "name": event.get("name") or "", "url": event.get("url"),
                     "code": course_code(cfg, event.get("courseid"), event.get("course") or "")})
    for assign in remote.get("assignments", []):
        due = assign.get("duedate") or 0
        if due < now or (assign["courseid"], due) in seen:
            continue
        rows.append({"at": due, "name": f"{assign['name']} (date limite)", "url": assign.get("url"),
                     "code": course_code(cfg, assign["courseid"], assign["course"])})
    return sorted(rows, key=lambda r: (r["at"], r["code"] or "", r["name"]))[:MAX_UPCOMING]


def _upcoming(cfg, remote, now):
    rows = upcoming(cfg, remote, now)
    if not rows:
        return ["Aucune échéance annoncée par Moodle."]
    lines = ["| Échéance | Cours | Événement |", "|---|---|---|"]
    for row in rows:
        label = web_link(row["url"], row["name"]).replace("|", "/")
        lines.append(f"| {util.fmt_date(row['at'], with_time=True, with_weekday=True)} | {cell(row['code'])} | {label} |")
    return lines


def entry_code(cfg, entry):
    return entry.get("code") or course_code(cfg, entry["courseid"], entry["course"])


def recent_announcements(remote, days, now):
    """Annonces de tous les cours publiées depuis `days` jours, la plus récente d'abord."""
    recent = [a for a in remote.get("announcements", []) if (a.get("time") or 0) >= now - days * 86400]
    return sorted(recent, key=lambda a: -a["time"])


def _announcements(cfg, state, days, now):
    remote = state.get("remote", {})
    recent = recent_announcements(remote, days, now)
    if not recent:
        return [f"Aucune annonce depuis {days} jours."]
    texts_by_id = remote.get("texts") or {}
    lines = []
    for ann in recent:
        clip = " · 📎" if ann.get("attachment") else ""
        lines.append(f"- {util.fmt_date(ann['time'], with_time=True, with_weekday=True)} · "
                     f"**{esc(course_code(cfg, ann['courseid'], ann['course']))}** · "
                     f"{web_link(ann['url'], ann['name'])}{clip}")
        # Résumé du modèle, ou annonce entière si elle est courte ; rien pour un simple début de texte
        summary, kind = summaries.for_snapshot(state.get("summaries") or {}, texts_by_id, ann)
        if summary and kind in ("modèle", "texte"):
            lines.append(f"    > {'🤖 ' if kind == 'modèle' else ''}{esc(summary)}")
    return lines


def _news(cfg, items, days, now):
    recent = [(k, e) for k, e in items.items()
              if e.get("status") in ("placed", "updated", "inbox", "zotero_pending")
              and (e.get("placed_at") or 0) >= now - days * 86400]
    if not recent:
        return [f"Aucun nouveau fichier depuis {days} jours."]
    groups = defaultdict(list)
    for key, entry in recent:
        groups[entry["placed_at"]].append(entry)
    lines = []
    for placed_at in sorted(groups, reverse=True):
        batch = groups[placed_at]
        if len(batch) > INITIAL_IMPORT_THRESHOLD:
            codes = sorted({entry_code(cfg, e) for e in batch})
            lines.append(f"- {util.fmt_date(placed_at)} · import de {len(batch)} fichiers ({', '.join(codes)}) — détail dans les cours ci-dessous")
            continue
        for entry in sorted(batch, key=lambda e: (e["course"], e["filename"])):
            code = entry_code(cfg, entry)
            where = {"inbox": "à classer", "zotero_pending": "à importer dans Zotero"}.get(
                entry["status"], f"`{entry.get('dest_dir')}`")
            flag = " · nouvelle version" if entry["status"] == "updated" else ""
            lines.append(f"- {util.fmt_date(placed_at)} · **{esc(code)}** · "
                         f"{file_link(cfg, entry['local_path'], entry['filename'])} → {where}{flag}")
    return lines


def _inbox(cfg, items):
    waiting = [e for e in items.values() if e.get("status") == "inbox"]
    if not waiting:
        return ["Rien à classer."]
    lines = []
    for entry in sorted(waiting, key=lambda e: (e["course"], e["section"], e["filename"])):
        code = entry_code(cfg, entry)
        reason = f" — {esc(entry['reason'])}" if entry.get("reason") else ""
        lines.append(f"- **{esc(code)}** · {file_link(cfg, entry['local_path'], entry['filename'])} "
                     f"· section « {esc(entry['section'])} »{reason}")
    return lines


def _zotero(cfg, items):
    waiting = [e for e in items.values() if e.get("status") == "zotero_pending"]
    if not waiting:
        return ["Aucun support de cours en attente d'import."]
    lines = ["Supports de cours mis de côté : à importer dans Zotero par un agent, après validation "
             "de la collection et des tags (ils ne vont pas dans l'arborescence).", ""]
    for entry in sorted(waiting, key=lambda e: (entry_code(cfg, e), e["section"], e["filename"])):
        also = (f" · une copie est aussi dans `{Path(entry['local_copy']).parent.as_posix()}`"
                if entry.get("local_copy") else "")
        lines.append(f"- **{esc(entry_code(cfg, entry))}** · "
                     f"{file_link(cfg, entry['local_path'], entry['filename'])} "
                     f"· section « {esc(entry['section'])} » · {web_link(entry.get('module_url'), 'Moodle')}{also}")
    return lines


def _texts_changed(cfg, remote, days, now):
    settings = cfg.data["texts"]
    recent = [t for t in texts.ordered(remote.get("texts") or {}, remote.get("courses", []))
              if t["kind"] != "announcement" and (t.get("changed_at") or 0) >= now - days * 86400]
    if not recent:
        return [f"Aucun texte modifié sur Moodle depuis {days} jours."]
    lines = []
    for record in sorted(recent, key=lambda t: -t["changed_at"]):
        flag = " · ⚖️ modalités de contrôle" if texts.is_mcc(record, settings) else ""
        lines.append(f"- {util.fmt_date(record['changed_at'])} · **{esc(course_code(cfg, record['courseid'], record['course']))}** · "
                     f"{texts.KIND_LABELS.get(record['kind'], record['kind'])} "
                     f"{web_link(record.get('url'), texts.heading(record))}{flag}")
    return lines


def _mcc(cfg, remote):
    """Modalités de contrôle : textes de Moodle recopiés en entier, un même texte n'apparaissant qu'une fois."""
    settings = cfg.data["texts"]
    groups = OrderedDict()
    for record in texts.ordered(remote.get("texts") or {}, remote.get("courses", [])):
        if cfg.course(record["courseid"]) and texts.is_mcc(record, settings):
            groups.setdefault(record["sha"], []).append(record)
    if not groups:
        return ["Aucun texte de Moodle ne décrit les modalités de contrôle."]
    lines = ["> [!important] Textes recopiés de Moodle (sections, étiquettes, pages) qui décrivent les contrôles "
             "et le calcul des notes. Moodle reste la référence ; un texte modifié y est signalé ci-dessus."]
    for records in groups.values():
        first = records[0]
        code = course_code(cfg, first["courseid"], first["course"])
        meta = [web_link(first.get("url"), "Moodle"), texts.date_text(first)]
        if cfg.texts_dir:
            path = texts.course_file(cfg, first["courseid"])
            meta.append(f"[textes du cours]({texts.link_from(cfg.index_path, path)})")
        lines += ["", f"### {esc(code)} — {esc(texts.heading(first))}", "", " · ".join(meta)]
        others = [f"{esc(course_code(cfg, r['courseid'], r['course']))} « {esc(texts.heading(r))} »" for r in records[1:]]
        if others:
            lines += ["", f"Texte identique dans : {', '.join(others)}."]
        lines += ["", first["text"]]
    return lines


def _file_line(cfg, entry):
    name = entry["filename"]
    status = entry.get("status")
    size = util.fmt_size(entry.get("filesize"))
    moodle = web_link(entry.get("module_url"), "Moodle")
    if status in ("zotero_pending", "in_zotero") and entry.get("local_path"):
        what = "à importer dans Zotero" if status == "zotero_pending" else "dans Zotero"
        return f"📚 {file_link(cfg, entry['local_path'], name)} · {size} · {what} · {moodle}"
    if status in ("placed", "present", "updated", "inbox") and entry.get("local_path"):
        parts = [file_link(cfg, entry["local_path"], name), size, util.fmt_date(entry.get("timemodified"))]
        folder = Path(entry["local_path"]).parent.as_posix()
        parts.append(f"`{folder}`")
        if status == "inbox":
            parts.append("à classer")
        if status == "updated":
            parts.append("nouvelle version")
        parts.append(moodle)
        return " · ".join(parts)
    if status == "listed":
        icon = "🎬" if entry.get("list_reason") == "video" else "📦"
        what = {"video": "vidéo non téléchargée", "trop lourd": "trop lourd, non téléchargé"}.get(
            entry.get("list_reason"), "non téléchargé")
        return f"{icon} {web_link(browser_url(entry.get('fileurl')), name)} · {size} · {what}"
    if status == "removed":
        local = f" · {file_link(cfg, entry['local_path'], 'copie locale')}" if entry.get("local_path") else ""
        return f"~~{esc(name)}~~ · retiré de Moodle{local}"
    if status == "missing":
        return f"{esc(name)} · supprimé localement (non retéléchargé) · {moodle}"
    if status == "error":
        return f"{esc(name)} · erreur de téléchargement, nouvel essai au prochain passage · {moodle}"
    return f"{esc(name)} · {moodle}"


def _course_block(cfg, course, items, assignments, announcements, course_texts=()):
    conf = cfg.course(course["id"])
    lines = [f"### {esc(conf['code'])} — {esc(course['fullname'])}", ""]
    header = (f"{web_link(course.get('url'), 'Ouvrir sur Moodle')} · dossier local : "
              f"{file_link(cfg, conf['dest'], conf['dest'])}")
    path = texts.course_file(cfg, course["id"]) if course_texts else None
    if path:
        header += f" · [textes Moodle]({texts.link_from(cfg.index_path, path)}) ({len(course_texts)})"
    lines.append(header)
    if announcements:
        lines += ["", "**Annonces récentes**"]
        for ann in sorted(announcements, key=lambda a: -a["time"]):
            clip = " · 📎" if ann.get("attachment") else ""
            lines.append(f"- {util.fmt_date(ann['time'])} · {web_link(ann['url'], ann['name'])}{clip}")
    by_section = OrderedDict((name, []) for name in course.get("sections", []))
    for entry in items:
        by_section.setdefault(entry["section"], []).append(entry)
    assigns_by_section = defaultdict(list)
    for assign in assignments:
        assigns_by_section[assign["section"]].append(assign)
        by_section.setdefault(assign["section"], [])
    # Textes de la section et pages : signalés ici, recopiés dans le fichier de textes du cours
    settings = cfg.data["texts"]
    notes_by_section = defaultdict(list)
    instructions = {r.get("cmid") for r in course_texts if r["kind"] == "assign"}
    for record in course_texts:
        if record["kind"] in ("section", "page", "label", "description"):
            notes_by_section[record.get("section")].append(record)
            by_section.setdefault(record.get("section"), [])
    for section, entries in by_section.items():
        notes = notes_by_section.get(section, [])
        if not entries and not assigns_by_section.get(section) and not notes:
            continue
        lines += ["", f"#### {esc(section)}"]
        for record in notes:
            flag = " · ⚖️ modalités de contrôle" if texts.is_mcc(record, settings) else ""
            if record["kind"] == "section":
                words = len(record["text"].split())
                lines.append(f"- 💬 Texte de la section ({words} mots){flag}")
            else:
                icon = {"page": "📄 Page", "label": "🏷️ Étiquette"}.get(record["kind"], "ℹ️ Description")
                lines.append(f"- {icon} : {web_link(record.get('url'), record.get('title') or '')}{flag}")
        modules = OrderedDict()
        for entry in sorted(entries, key=lambda e: (e["module"], e["filepath"], e["filename"])):
            modules.setdefault(entry["cmid"], []).append(entry)
        for files in modules.values():
            first = files[0]
            if first["modname"] == "folder" or len(files) > 1:
                lines.append(f"- 📁 **{esc(first['module'])}** · {web_link(first.get('module_url'), 'Moodle')}")
                lines += [f"    - {_file_line(cfg, e)}" for e in files]
            else:
                lines.append(f"- {_file_line(cfg, first)}")
        for assign in assigns_by_section.get(section, []):
            due = (f"à rendre le {util.fmt_date(assign['duedate'], with_time=True, with_weekday=True)}"
                   if assign.get("duedate") else "sans date limite")
            told = " · consignes dans les textes du cours" if assign.get("cmid") in instructions else ""
            lines.append(f"- 📝 Devoir : {web_link(assign['url'], assign['name'])} — {due}{told}")
    return lines + [""]


def render(cfg, state, now=None):
    now = int(now or time.time())
    remote = state.get("remote", {})
    items = state.get("items", {})
    index_conf = cfg.data["index"]
    last = state.get("last_sync")
    last_text = (util.fmt_date(util.parse_iso(last), with_time=True) if last else "jamais")
    lines = [
        "---",
        "type: index_moodle",
        f"source: {cfg.site}",
        f"derniere_synchro: {last or ''}",
        "genere_par: moodle-bridge",
        "---",
        "",
        f"# {index_conf.get('title') or 'Moodle'}",
        "",
        f"> [!info] Index généré automatiquement par moodle-bridge — dernière synchronisation : {last_text}.",
        "> Ne pas modifier ce fichier : il est réécrit à chaque passage.",
    ]
    show_texts = cfg.texts_enabled or bool(remote.get("texts"))
    if show_texts:
        lines.append("> Modalités de contrôle des connaissances recopiées de Moodle : [[#Modalités de contrôle]].")
    if state.get("last_error"):
        lines += ["", f"> [!warning] Dernier passage en échec : {esc(state['last_error'])}"]
    lines += ["", "## À venir", ""] + _upcoming(cfg, remote, now)
    days = int(index_conf.get("news_days") or 7)
    lines += ["", f"## Annonces récentes ({days} derniers jours)", ""] + _announcements(cfg, state, days, now)
    lines += ["", f"## Nouveaux fichiers ({days} derniers jours)", ""] + _news(cfg, items, days, now)
    if show_texts:
        lines += ["", f"## Textes modifiés sur Moodle ({days} derniers jours)", ""] + _texts_changed(cfg, remote, days, now)
    lines += ["", "## À classer", ""] + _inbox(cfg, items)
    if cfg.zotero_enabled or any(e.get("status") == "zotero_pending" for e in items.values()):
        lines += ["", "## À importer dans Zotero", ""] + _zotero(cfg, items)
    unconfigured = remote.get("unconfigured") or {}
    if unconfigured:
        lines += ["", "## Cours non configurés", ""]
        lines += [f"- {esc(name)} (id {cid}) — fichiers ignorés tant que le cours n'a pas de destination"
                  for cid, name in sorted(unconfigured.items(), key=lambda kv: kv[1] or "")]
    if show_texts:
        lines += ["", "## Modalités de contrôle", ""] + _mcc(cfg, remote)
    lines += ["", "## Cours", ""]
    by_course = defaultdict(list)
    for entry in items.values():
        by_course[entry["courseid"]].append(entry)
    assigns = defaultdict(list)
    for assign in remote.get("assignments", []):
        assigns[assign["courseid"]].append(assign)
    anns = defaultdict(list)
    for ann in remote.get("announcements", []):
        anns[ann["courseid"]].append(ann)
    course_texts = defaultdict(list)
    for record in texts.ordered(remote.get("texts") or {}, remote.get("courses", [])):
        course_texts[record["courseid"]].append(record)
    for course in remote.get("courses", []):
        if cfg.course(course["id"]) is None:
            continue
        lines += _course_block(cfg, course, by_course.get(course["id"], []),
                               assigns.get(course["id"], []), anns.get(course["id"], []),
                               course_texts.get(course["id"], []))
    return "\n".join(lines).rstrip() + "\n"


def write(cfg, state, now=None):
    path = cfg.index_path
    if not path:
        return None
    util.write_text_atomic(path, render(cfg, state, now))
    return path
