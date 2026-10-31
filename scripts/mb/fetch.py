"""Photographie du Moodle distant : cours, fichiers, textes, devoirs, échéances et annonces (lecture seule)."""

import html
import mimetypes
import time

from .api import MoodleError, TokenError
from .texts import html_to_md

PAUSE_BETWEEN_COURSES = 0.3  # secondes, pour ménager le serveur


def clean(text):
    return html.unescape(text or "").strip()


def browser_url(fileurl):
    """Lien ouvrable dans un navigateur connecté (sans jeton) à partir d'une URL de l'API."""
    url = (fileurl or "").replace("/webservice/pluginfile.php", "/pluginfile.php")
    return url.replace("?forcedownload=1", "").replace("&forcedownload=1", "")


def _file_item(course, section, section_num, module, entry, kind="file"):
    filepath = entry.get("filepath") or "/"
    filename = clean(entry.get("filename"))
    return {
        "key": f"{course['id']}/{module['id']}{filepath}{filename}",
        "courseid": course["id"],
        "course": course.get("shortname") or course.get("fullname"),
        "cmid": module["id"],
        "modname": module.get("modname", ""),
        "module": clean(module.get("name")),
        "module_url": module.get("url") or "",
        "section": section,
        "section_num": section_num,
        "filepath": filepath,
        "filename": filename,
        "filesize": int(entry.get("filesize") or 0),
        "timemodified": int(entry.get("timemodified") or 0),
        "mimetype": entry.get("mimetype") or mimetypes.guess_type(filename)[0] or "",
        "fileurl": entry.get("fileurl") or "",
        "kind": kind,
    }


def _text(texts, tid, kind, course, title, html_parts, url, section=None, section_num=None, section_pos=None,
          position=-1, cmid=None, **extra):
    """Ajoute un texte converti en Markdown, s'il n'est pas vide."""
    text = "\n\n".join(part for part in (html_to_md(h) for h in html_parts) if part)
    if not text:
        return
    texts[tid] = {"kind": kind, "courseid": course["id"], "course": course.get("shortname"), "title": title,
                  "section": section, "section_num": section_num, "section_pos": section_pos, "position": position,
                  "cmid": cmid, "url": url, "text": text, "time": None, **extra}


def snapshot(client, log=print, announcements_per_course=5, with_texts=True):
    info = client.call("core_webservice_get_site_info")
    raw_courses = client.call("core_enrol_get_users_courses", userid=info["userid"])
    courses, items, assignments, events, announcements = [], {}, [], [], []
    texts, failed_texts = {}, []
    cm_section = {}

    for course in sorted(raw_courses, key=lambda c: c.get("fullname", "")):
        contents = client.call("core_course_get_contents", courseid=course["id"])
        sections = []
        for pos, section in enumerate(contents):
            name = clean(section.get("name")) or f"Section {section.get('section')}"
            sections.append(name)
            where = {"section": name, "section_num": section.get("section"), "section_pos": pos}
            section_url = f"{client.site}/course/view.php?id={course['id']}#section-{section.get('section')}"
            if with_texts:
                _text(texts, f"section:{course['id']}:{section.get('id', section.get('section'))}", "section", course,
                      name, [section.get("summary")], section_url, **where)
            for position, module in enumerate(section.get("modules", [])):
                cm_section[module["id"]] = (name, section.get("section"), pos, position)
                # Consignes des devoirs et introduction des pages : relevées avec le devoir ou la page
                if with_texts and module.get("description") and module.get("modname") not in ("assign", "page"):
                    kind = "label" if module.get("modname") == "label" else "description"
                    _text(texts, f"module:{module['id']}", kind, course, clean(module.get("name")),
                          [module.get("description")], module.get("url") or section_url, position=position,
                          cmid=module["id"], modname=module.get("modname"), **where)
                for entry in module.get("contents") or []:
                    if entry.get("type") == "file":
                        item = _file_item(course, name, section.get("section"), module, entry)
                        items[item["key"]] = item
        courses.append({
            "id": course["id"],
            "shortname": course.get("shortname"),
            "fullname": clean(course.get("fullname")),
            "url": f"{client.site}/course/view.php?id={course['id']}",
            "sections": sections,
            "enddate": course.get("enddate"),
        })
        time.sleep(PAUSE_BETWEEN_COURSES)

    by_id = {c["id"]: c for c in courses}
    ids = list(by_id)

    # Énoncés joints aux devoirs : absents de core_course_get_contents
    try:
        answer = client.call("mod_assign_get_assignments", courseids=ids)
        for entry in answer.get("courses", []):
            course = by_id.get(entry["id"])
            if not course:
                continue
            for assign in entry.get("assignments", []):
                section, section_num, pos, position = cm_section.get(assign["cmid"], ("Devoirs", None, None, -1))
                module = {"id": assign["cmid"], "modname": "assign", "name": assign.get("name"),
                          "url": f"{client.site}/mod/assign/view.php?id={assign['cmid']}"}
                assignments.append({
                    "courseid": course["id"], "course": course["shortname"], "cmid": assign["cmid"],
                    "name": clean(assign.get("name")), "section": section,
                    "duedate": assign.get("duedate") or 0, "cutoffdate": assign.get("cutoffdate") or 0,
                    "url": module["url"],
                })
                for attachment in assign.get("introattachments", []):
                    item = _file_item(course, section, section_num, module, attachment, kind="assign_intro")
                    items[item["key"]] = item
                if with_texts:
                    _text(texts, f"assign:{assign['cmid']}", "assign", course, clean(assign.get("name")),
                          [assign.get("intro"), assign.get("activity")], module["url"], section, section_num,
                          pos, position, assign["cmid"], time=assign.get("timemodified") or None,
                          duedate=assign.get("duedate") or 0)
    except TokenError:
        raise
    except MoodleError as exc:
        failed_texts.append("assign")
        log(f"  ! devoirs indisponibles : {exc}")

    # Pages : leur contenu n'est fourni que par cette fonction (dans core_course_get_contents, un simple index.html)
    if with_texts:
        try:
            answer = client.call("mod_page_get_pages_by_courses", courseids=ids)
            for page in answer.get("pages", []):
                course = by_id.get(page.get("course"))
                if not course:
                    continue
                cmid = page["coursemodule"]
                section, section_num, pos, position = cm_section.get(cmid, (None, page.get("section"), None, -1))
                _text(texts, f"page:{cmid}", "page", course, clean(page.get("name")),
                      [page.get("intro"), page.get("content")], f"{client.site}/mod/page/view.php?id={cmid}",
                      section, section_num, pos, position, cmid, time=page.get("timemodified") or None)
        except TokenError:
            raise
        except MoodleError as exc:
            failed_texts.append("page")
            log(f"  ! pages indisponibles : {exc}")

    try:
        answer = client.call("core_calendar_get_calendar_upcoming_view")
        for event in answer.get("events", []):
            course = event.get("course") or {}
            events.append({
                "id": event.get("id"), "name": clean(event.get("name")),
                "timestart": event.get("timestart") or 0, "courseid": course.get("id"),
                "course": course.get("shortname"), "url": event.get("url") or event.get("viewurl") or "",
                "eventtype": event.get("eventtype"), "modulename": event.get("modulename"),
            })
    except TokenError:
        raise
    except MoodleError as exc:
        log(f"  ! calendrier indisponible : {exc}")

    if announcements_per_course:
        try:
            forums = client.call("mod_forum_get_forums_by_courses", courseids=ids)
            for forum in forums:
                if forum.get("type") != "news" or forum.get("course") not in by_id:
                    continue
                answer = client.call("mod_forum_get_forum_discussions", forumid=forum["id"],
                                     page=0, perpage=announcements_per_course)
                for disc in answer.get("discussions", []):
                    announcement = {
                        "courseid": forum["course"], "course": by_id[forum["course"]]["shortname"],
                        "name": clean(disc.get("name") or disc.get("subject")),
                        "time": disc.get("created") or disc.get("timemodified") or 0,
                        "author": disc.get("userfullname", ""),
                        "attachment": bool(disc.get("attachment")) and disc.get("attachment") not in ("0", 0),
                        "url": f"{client.site}/mod/forum/discuss.php?d={disc.get('discussion')}",
                    }
                    announcements.append(announcement)
                    if with_texts:
                        _text(texts, f"announcement:{disc.get('discussion')}", "announcement",
                              by_id[forum["course"]], announcement["name"], [disc.get("message")],
                              announcement["url"], time=announcement["time"],
                              modified=disc.get("modified") or disc.get("timemodified") or None,
                              author=announcement["author"])
        except TokenError:
            raise
        except MoodleError as exc:
            failed_texts.append("announcement")
            log(f"  ! annonces indisponibles : {exc}")

    return {
        "fetched_at": int(time.time()),
        "user": info.get("fullname"),
        "sitename": info.get("sitename"),
        "courses": courses,
        "items": items,
        "assignments": assignments,
        "events": events,
        "announcements": announcements,
        "texts": texts,
        "failed_texts": failed_texts,
    }
