"""Coffre Obsidian : le contenu de Moodle rangé en notes reliées entre elles, régénéré à chaque passage.

- `Accueil` : la semaine à venir, les dernières annonces, les nouveaux documents, ce qui attend une action ;
- `Matières/<CODE>` : une note par matière (un cours Moodle, ou la partie qu'une route envoie dans un autre
  dossier) avec ses échéances, annonces, modalités de contrôle, notes et tout son contenu, section par section ;
- `Devoirs/<CODE> — <devoir>` : date limite, consignes et pièces jointes de chaque devoir ;
- `Échéances`, `Annonces`, `Documents`, `Modalités de contrôle` et `Notes` : les mêmes informations, tous cours
  confondus.

Tout est déterministe : aucun modèle de langage n'intervient ici, et une note n'est réécrite que si son contenu
change. Les résumés d'annonces n'apparaissent que si l'IA les a déjà faits. Le pont ne touche qu'aux notes qu'il
a générées (propriété `genere_par`) : les notes personnelles posées dans le coffre restent intactes.
"""

import json
import os
import re
import time
import urllib.parse
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from pathlib import Path

from . import summaries, texts, util
from .fetch import browser_url
from .index import INITIAL_IMPORT_THRESHOLD, esc, recent_announcements, upcoming

try:
    from . import notes as grades
except ImportError:  # édition sans relevé des notes
    grades = None

MARKER = texts.MARKER
SUBJECTS, ASSIGNMENTS = "Matières", "Devoirs"
HOME, DEADLINES, NEWS, DOCUMENTS, MCC, GRADES = (
    "Accueil", "Échéances", "Annonces", "Documents", "Modalités de contrôle", "Notes")
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]
WEEK = 7 * 86400
DOCUMENT_DAYS = 30
HOME_ANNOUNCEMENTS = 8
SUBJECT_ANNOUNCEMENTS = 5
LOCAL = ("placed", "present", "updated", "inbox", "zotero_pending", "in_zotero")


# ---------------------------------------------------------------- mise en forme

def find_root(directory):
    """Racine du coffre Obsidian : le dossier, ou le premier de ses parents, qui contient `.obsidian`."""
    directory = Path(directory).absolute()
    for folder in (directory, *directory.parents):
        if (folder / ".obsidian").is_dir():
            return folder
    return directory


def iso(ts):
    """Date d'une propriété Obsidian (type date et heure)."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M") if ts else None


def frontmatter(props):
    lines = ["---"]
    for key, value in props.items():
        if value not in (None, "", []):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return lines + [MARKER, "---", ""]


def label(text):
    """Texte affiché d'un lien interne, sans les caractères qui terminent un lien Obsidian."""
    return " ".join(str(text or "").replace("|", "/").replace("[", "(").replace("]", ")").split())


def note_name(text, maxlen=90):
    return util.safe_name(re.sub(r"[#^\[\]|]+", " ", text or ""), maxlen)


def cell(text):
    return esc(" ".join(str(text or "").split())).replace("|", "\\|")


def web(url, text, table=False):
    shown = cell(text) if table else esc(text)
    return f"[{shown}]({url})" if url else shown


def callout(kind, title, body, folded=True):
    lines = [f"> [!{kind}]{'-' if folded else ''} {title}".rstrip()]
    return lines + [f"> {line}".rstrip() for line in (body or "").strip().splitlines()]


def when(ts, year=None):
    """« mer. 23/09 14:00 » ; l'année s'ajoute si elle n'est pas l'année en cours (ou si `year` est vrai)."""
    moment = datetime.fromtimestamp(ts)
    if year is None:
        year = moment.year != datetime.now().year
    return f"{util.WEEKDAYS[moment.weekday()]} {moment.strftime('%d/%m/%Y %H:%M' if year else '%d/%m %H:%M')}"


def until(ts, now):
    days = (date.fromtimestamp(ts) - date.fromtimestamp(now)).days
    return {0: "aujourd'hui", 1: "demain"}.get(days, f"dans {days} jours" if days > 0 else "passé")


def month(ts):
    moment = datetime.fromtimestamp(ts)
    return f"{MONTHS[moment.month - 1].capitalize()} {moment.year}"


def table(header, rows):
    if not rows:
        return []
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in rows]


# ---------------------------------------------------------------- coffre

class Vault:
    def __init__(self, cfg, state, now=None):
        self.cfg = cfg
        self.state = state
        self.now = int(now or time.time())
        self.remote = state.get("remote", {})
        self.items = state.get("items", {})
        self.texts_by_id = self.remote.get("texts") or {}
        self.summaries = state.get("summaries") or {}
        self.dir = Path(cfg.vault_dir).absolute()
        self.root = find_root(self.dir)
        rel = Path(os.path.relpath(self.dir, self.root)).as_posix()
        self.prefix = "" if rel == "." else rel + "/"
        self.title = cfg.data["vault"].get("title") or "Moodle"
        self.days = int(cfg.data["index"].get("news_days") or 7)
        self.mcc_settings = cfg.data["texts"]
        self.courses = {c["id"]: c for c in self.remote.get("courses", [])}
        self.subjects = OrderedDict()
        self.section_subject = {}
        for course in self.remote.get("courses", []):
            for section in course.get("sections", []):
                self.subject_of(course["id"], section)
        self.by_subject = defaultdict(lambda: defaultdict(list))
        for entry in sorted(self.items.values(), key=lambda e: (e.get("module") or "", e["filepath"], e["filename"])):
            self._add("files", self.subject_of(entry["courseid"], entry.get("section")), entry)
        for assign in self.remote.get("assignments", []):
            self._add("assignments", self.subject_of(assign["courseid"], assign.get("section")), assign)
        for ann in sorted(self.remote.get("announcements", []), key=lambda a: -(a.get("time") or 0)):
            self._add("announcements", self.subject_of(ann["courseid"]), ann)
        for record in texts.ordered(self.texts_by_id, self.remote.get("courses", [])):
            if record["kind"] != "announcement":
                self._add("texts", self.subject_of(record["courseid"], record.get("section")), record)
        self.instructions = {r.get("cmid"): r for r in self.texts_by_id.values() if r["kind"] == "assign"}
        self.assignment_notes = self._assignment_notes()
        self.deadlines = [dict(r, subject=self.subject_of(r["courseid"], r.get("section")))
                          for r in upcoming(cfg, self.remote, self.now, limit=None)]
        self.grades = self._grades()

    def _add(self, kind, code, value):
        if code:
            self.by_subject[code][kind].append(value)

    # -------------------------------------------------------- matières

    def subject_of(self, courseid, section=None):
        """Code de la matière d'un cours et d'une section : celui du cours, ou celui d'une route qui envoie
        cette section hors du dossier du cours (une matière distincte hébergée dans le même cours Moodle)."""
        key = (courseid, section)
        if key not in self.section_subject:
            conf = self.cfg.course(courseid)
            if not conf:
                return None
            course = self.courses.get(courseid) or {
                "id": courseid, "fullname": conf.get("name") or conf["code"],
                "url": f"{self.cfg.site}/course/view.php?id={courseid}", "sections": []}
            code = self._subject(conf["code"], course, conf["dest"])
            route = self.cfg.conf_for({"courseid": courseid, "section": section}) if section is not None else None
            if route and route["code"] != code:
                try:
                    Path(route["root"]).relative_to(conf["root"])  # sous-dossier du cours : même matière
                except ValueError:
                    code = self._subject(route["code"], course, route["dest"], part=True)
                    self.subjects[code]["sections"].append(section)
            self.section_subject[key] = code
        return self.section_subject[key]

    def _subject(self, code, course, dest, part=False):
        if code not in self.subjects:
            self.subjects[code] = {"code": code, "course": course, "dest": dest, "part": part, "sections": [],
                                   "note": f"{SUBJECTS}/{note_name(code, 60)}"}
        return code

    def subject_link(self, code, in_table=False):
        if code in self.subjects:
            return self.link(self.subjects[code]["note"], code, in_table)
        return cell(code) if in_table else esc(code)

    def _assignment_notes(self):
        names, used = {}, set()
        for assign in sorted(self.remote.get("assignments", []), key=lambda a: a["cmid"]):
            code = self.subject_of(assign["courseid"], assign.get("section"))
            if not code:
                continue
            name = note_name(f"{code} — {assign['name']}")
            if name.lower() in used:
                name = note_name(f"{code} — {assign['name']} ({assign['cmid']})")
            used.add(name.lower())
            names[assign["cmid"]] = f"{ASSIGNMENTS}/{name}"
        return names

    def _grades(self):
        """Notes relevées, par matière ; None si le relevé des notes n'existe pas ou n'a jamais servi."""
        if not grades:
            return None
        conf = grades.settings(self.cfg)
        state = grades.load_state(self.cfg)
        if not conf.get("enabled") and not state["items"]:
            return None
        mapping = (conf.get("workbook") or {}).get("map") or {}
        rows = defaultdict(list)
        for key, entry in sorted(state["items"].items(), key=lambda kv: kv[1].get("name") or ""):
            code = self.subject_of(entry.get("courseid"))
            # Un élément relié au classeur sous le code d'une autre matière connue (une route) y est rangé
            element = (mapping.get(key) or {}).get("element")
            if element in self.subjects:
                code = element
            rows[code or entry.get("code") or "?"].append(entry)
        return rows

    # -------------------------------------------------------- liens

    def link(self, rel, text, in_table=False):
        """Lien interne vers une note générée (`rel` : chemin dans le dossier du coffre, sans .md)."""
        separator = "\\|" if in_table else "|"  # dans un tableau, la barre du lien doit être échappée
        return f"[[{self.prefix}{rel}{separator}{label(text)}]]"

    def file(self, here, target, text, in_table=False):
        """Lien vers un fichier local : relatif s'il est dans le coffre (Obsidian l'ouvre), sinon file://."""
        target = self.cfg.resolve(target).absolute()
        shown = cell(text) if in_table else esc(text)
        try:
            target.relative_to(self.root)
            href = urllib.parse.quote(Path(os.path.relpath(target, (self.dir / here).parent)).as_posix())
        except ValueError:
            href = target.as_uri()
        return f"[{shown}]({href})"

    @property
    def pages(self):
        pages = [HOME, DEADLINES, NEWS, DOCUMENTS]
        if self.cfg.texts_enabled or self.texts_by_id:
            pages.append(MCC)
        if self.grades is not None:
            pages.append(GRADES)
        return pages

    # -------------------------------------------------------- morceaux communs

    def deadline_rows(self, rows, with_subject=True, relative=False):
        out = []
        for row in rows:
            what = (self.link(self.assignment_notes[row["cmid"]], row["name"], True)
                    if row.get("cmid") in self.assignment_notes else cell(row["name"]))
            moodle = f" · {web(row['url'], 'Moodle', True)}" if row.get("url") else ""
            moment = when(row["at"]) + (f" ({until(row['at'], self.now)})" if relative else "")
            out.append([moment] + ([self.subject_link(row["subject"], True)] if with_subject else []) + [what + moodle])
        return out

    def announcement(self, ann, with_subject=True):
        who = f"{self.subject_link(self.subject_of(ann['courseid']))} · " if with_subject else ""
        clip = " · 📎" if ann.get("attachment") else ""
        lines = [f"- {when(ann['time'])} · {who}{web(ann.get('url'), ann.get('name'))}{clip}"]
        summary, kind = summaries.for_snapshot(self.summaries, self.texts_by_id, ann)
        if summary and kind in ("modèle", "texte"):
            lines.append(f"    > {'🤖 ' if kind == 'modèle' else ''}{esc(summary)}")
        return lines

    def file_row(self, here, entry, with_subject=False):
        name = entry["filename"]
        status = entry.get("status")
        shown = f"{entry['module']} › {name}" if entry.get("modname") == "folder" and entry.get("module") else name
        local = entry.get("local_path")
        notes = []
        if status in LOCAL and local:
            doc = self.file(here, local, shown, True)
            if status in ("zotero_pending", "in_zotero"):
                notes.append("📚 à importer dans Zotero" if status == "zotero_pending" else "📚 dans Zotero")
            else:
                notes.append(f"`{cell(Path(local).parent.as_posix())}`")
                notes += {"inbox": ["à classer"], "updated": ["nouvelle version"]}.get(status, [])
        elif status == "listed":
            doc = web(browser_url(entry.get("fileurl")), shown, True)
            notes.append({"video": "🎬 vidéo, non téléchargée", "trop lourd": "📦 trop lourd, non téléchargé"}.get(
                entry.get("list_reason"), "non téléchargé"))
        elif status == "removed":
            doc = f"~~{cell(shown)}~~"
            notes.append("retiré de Moodle" + (f" · {self.file(here, local, 'copie locale', True)}" if local else ""))
        else:
            doc = cell(shown)
            notes.append({"missing": "supprimé localement, non retéléchargé",
                          "error": "erreur de téléchargement, nouvel essai au prochain passage"}.get(status, ""))
        if entry.get("module_url"):
            notes.append(web(entry["module_url"], "Moodle", True))
        row = [doc, util.fmt_size(entry.get("filesize")), util.fmt_date(entry.get("timemodified")),
               " · ".join(n for n in notes if n)]
        if with_subject:
            row.insert(1, self.subject_link(self.subject_of(entry["courseid"], entry.get("section")), True))
        return row

    def grade_cells(self, entry):
        """Note et note sur 20 affichables : la valeur d'une note encore mystère n'est jamais lue."""
        status = entry.get("status")
        if status == "mystery":
            mystery = entry.get("mystery") or grades.FALLBACK
            teaser = f" — *{cell(mystery['teaser'])}*" if mystery.get("teaser") else ""
            return f"{mystery['emoji']} à découvrir dans l'application{teaser}", "—"
        if status in ("revealed", "known") and entry.get("grade"):
            score = grades.on20(entry["grade"])
            return cell(grades.display(entry["grade"])), "—" if score is None else grades.fr_number(score)
        return "pas encore notée", "—"

    def grade_rows(self, entries, with_workbook=False):
        rows = []
        for entry in entries:
            value, score = self.grade_cells(entry)
            moment = util.parse_iso(entry.get("revealed_at") or entry.get("detected_at"))
            row = [web(entry.get("url"), entry.get("name"), True), value, score, util.fmt_date(moment)]
            if with_workbook:
                wb = entry.get("workbook") if entry.get("status") == "revealed" else None
                row.append(cell(grades.workbook_label(wb) or "") if wb else "")
            rows.append(row)
        return rows

    def mcc_groups(self, records):
        groups = OrderedDict()
        for record in records:
            if texts.is_mcc(record, self.mcc_settings):
                groups.setdefault(record["sha"], []).append(record)
        return groups

    # -------------------------------------------------------- pages

    def home(self):
        last = util.parse_iso(self.state.get("last_sync"))
        lines = frontmatter({"type": "moodle-accueil", "site": self.cfg.site, "derniere_synchro": iso(last),
                             "tags": ["moodle"]})
        lines += [f"# {self.title}", "",
                  f"> [!info] Généré par Moodle Bridge depuis {self.cfg.site} — dernière synchronisation : "
                  f"{util.fmt_date(last, with_time=True) if last else 'jamais'}.",
                  "> Ces notes sont réécrites à chaque passage : écris les tiennes à côté, elles ne sont jamais touchées."]
        if self.state.get("last_error"):
            lines += ["", f"> [!warning] Dernier passage en échec : {esc(self.state['last_error'])}"]
        lines += ["", " · ".join(self.link(p, p) for p in self.pages if p != HOME)]

        week = [r for r in self.deadlines if r["at"] <= self.now + WEEK]
        lines += ["", "## Cette semaine", ""]
        lines += table(["Quand", "Matière", "Quoi"], self.deadline_rows(week, relative=True)) or [
            "Rien d'annoncé pour les 7 prochains jours."]
        later = len(self.deadlines) - len(week)
        if later:
            lines += ["", f"Et {later} échéance(s) plus tard : {self.link(DEADLINES, DEADLINES)}."]

        recent = [a for a in recent_announcements(self.remote, self.days, self.now) if self.subject_of(a["courseid"])]
        lines += ["", f"## Annonces des {self.days} derniers jours", ""]
        for ann in recent[:HOME_ANNOUNCEMENTS]:
            lines += self.announcement(ann)
        if not recent:
            lines.append(f"Aucune annonce depuis {self.days} jours.")
        elif len(recent) > HOME_ANNOUNCEMENTS:
            lines += ["", f"Toutes les annonces : {self.link(NEWS, NEWS)}."]

        lines += ["", f"## Nouveaux documents ({self.days} derniers jours)", ""]
        lines += self.news_lines(f"{HOME}.md", self.days)

        waiting = []
        counts = defaultdict(int)
        for entry in self.items.values():
            counts[entry.get("status")] += 1
        if counts["inbox"]:
            waiting.append(f"- {counts['inbox']} fichier(s) à classer : {self.link(DOCUMENTS + '#À classer', 'Documents')}")
        if counts["zotero_pending"]:
            waiting.append(f"- {counts['zotero_pending']} support(s) de cours à importer dans Zotero : "
                           f"{self.link(DOCUMENTS + '#À importer dans Zotero', 'Documents')}")
        mysteries = sum(1 for rows in (self.grades or {}).values() for e in rows if e.get("status") == "mystery")
        if mysteries:
            waiting.append(f"- {mysteries} note(s) à découvrir dans l'application : {self.link(GRADES, GRADES)}")
        unconfigured = self.remote.get("unconfigured") or {}
        if unconfigured:
            waiting.append(f"- cours non configuré(s), fichiers ignorés : {esc(', '.join(sorted(map(str, unconfigured.values()))))}")
        lines += ["", "## En attente", ""] + (waiting or ["Rien n'attend d'action."])

        rows = []
        for code, subject in self.subjects.items():
            data = self.by_subject[code]
            documents = sum(1 for e in data["files"] if e.get("status") in LOCAL)
            due = sum(1 for a in data["assignments"] if (a.get("duedate") or 0) >= self.now)
            nxt = next((r for r in self.deadlines if r["subject"] == code), None)
            rows.append([self.subject_link(code, True), cell(self.subject_title(code)), str(documents), str(due),
                         when(nxt["at"]) if nxt else "—"])
        lines += ["", "## Matières", ""]
        lines += table(["Matière", "Intitulé", "Documents", "Devoirs à rendre", "Prochaine échéance"], rows) or [
            "Aucun cours suivi."]
        return lines

    def news_lines(self, here, days):
        recent = [e for e in self.items.values()
                  if e.get("status") in ("placed", "updated", "inbox", "zotero_pending")
                  and (e.get("placed_at") or 0) >= self.now - days * 86400]
        if not recent:
            return [f"Aucun nouveau fichier depuis {days} jours."]
        batches = defaultdict(list)
        for entry in recent:
            batches[entry["placed_at"]].append(entry)
        lines = []
        for placed_at in sorted(batches, reverse=True):
            batch = batches[placed_at]
            if len(batch) > INITIAL_IMPORT_THRESHOLD:
                codes = sorted({self.subject_of(e["courseid"], e.get("section")) or e["course"] for e in batch})
                lines.append(f"- {util.fmt_date(placed_at)} · import de {len(batch)} fichiers ({esc(', '.join(codes))})")
                continue
            for entry in sorted(batch, key=lambda e: (e["course"], e["filename"])):
                code = self.subject_of(entry["courseid"], entry.get("section"))
                where = {"inbox": "à classer", "zotero_pending": "à importer dans Zotero"}.get(
                    entry["status"], f"`{esc(entry.get('dest_dir') or '')}`")
                flag = " · nouvelle version" if entry["status"] == "updated" else ""
                lines.append(f"- {util.fmt_date(placed_at)} · {self.subject_link(code)} · "
                             f"{self.file(here, entry['local_path'], entry['filename'])} → {where}{flag}")
        return lines

    def deadlines_page(self):
        lines = frontmatter({"type": "moodle-echeances", "tags": ["moodle"]})
        lines += [f"# {DEADLINES}", "", f"Retour : {self.link(HOME, HOME)}", "", "## À venir", ""]
        lines += table(["Quand", "Matière", "Quoi"], self.deadline_rows(self.deadlines)) or [
            "Aucune échéance annoncée par Moodle."]
        assigns = [a for a in self.remote.get("assignments", []) if a.get("cmid") in self.assignment_notes]
        assigns.sort(key=lambda a: ((a.get("duedate") or 0) < self.now, not a.get("duedate"),
                                    (a.get("duedate") or 0) * (1 if (a.get("duedate") or 0) >= self.now else -1)))
        rows = [[self.link(self.assignment_notes[a["cmid"]], a["name"], True),
                 self.subject_link(self.subject_of(a["courseid"], a.get("section")), True),
                 when(a["duedate"]) if a.get("duedate") else "—", self.assignment_status(a)] for a in assigns]
        lines += ["", "## Tous les devoirs", ""]
        lines += table(["Devoir", "Matière", "Date limite", "Statut"], rows) or ["Aucun devoir sur Moodle."]
        return lines

    def assignment_status(self, assign):
        due = assign.get("duedate") or 0
        if not due:
            return "sans date limite"
        return "à rendre" if due >= self.now else "date passée"

    def news_page(self):
        lines = frontmatter({"type": "moodle-annonces", "tags": ["moodle"]})
        lines += [f"# {NEWS}", "", f"Retour : {self.link(HOME, HOME)}"]
        current = None
        anns = [a for a in sorted(self.remote.get("announcements", []), key=lambda a: -(a.get("time") or 0))
                if self.subject_of(a["courseid"])]
        for ann in anns:
            if month(ann["time"]) != current:
                current = month(ann["time"])
                lines += ["", f"## {current}"]
            code = self.subject_of(ann["courseid"])
            lines += ["", f"### {util.fmt_date(ann['time'])} · {code} — {label(ann.get('name'))}", ""]
            meta = [self.subject_link(code), web(ann.get("url"), "Moodle"), when(ann["time"])]
            if ann.get("author"):
                meta.append(esc(ann["author"]))
            if ann.get("attachment"):
                meta.append("📎 pièce jointe sur Moodle")
            lines.append(" · ".join(meta))
            summary, kind = summaries.for_snapshot(self.summaries, self.texts_by_id, ann)
            record = self.texts_by_id.get(summaries.text_id(ann))
            if kind == "modèle":
                lines += ["", *callout("abstract", "Résumé (IA)", esc(summary), folded=False)]
            if record:
                short = len(record["text"]) <= summaries.MAX_CHARS
                lines += ["", *callout("quote", "Texte de l'annonce", record["text"], folded=kind == "modèle" or not short)]
        if not anns:
            lines += ["", "Aucune annonce relevée."]
        return lines

    def documents_page(self):
        here = f"{DOCUMENTS}.md"
        lines = frontmatter({"type": "moodle-documents", "tags": ["moodle"]})
        lines += [f"# {DOCUMENTS}", "", f"Retour : {self.link(HOME, HOME)}",
                  "", f"## Nouveaux documents ({DOCUMENT_DAYS} derniers jours)", ""]
        lines += self.news_lines(here, DOCUMENT_DAYS)
        header = ["Document", "Matière", "Taille", "Modifié", "Où"]
        sections = [
            ("À classer", "inbox", "Rien à classer.",
             "Le pont n'a pas su choisir leur dossier : déplace-les où il faut, la synchronisation suivante les "
             "retrouve par leur empreinte."),
            ("À importer dans Zotero", "zotero_pending", "Aucun support de cours en attente.",
             "Supports de cours mis de côté : ils vont dans Zotero, pas dans l'arborescence."),
            ("Non téléchargés", "listed", "Rien.", "Vidéos et fichiers trop lourds : seulement listés, avec leur lien Moodle."),
            ("Retirés de Moodle", "removed", "Rien.", "La copie locale, s'il y en a une, est conservée."),
        ]
        for title, status, empty, intro in sections:
            entries = sorted((e for e in self.items.values() if e.get("status") == status and self.subject_of(e["courseid"])),
                             key=lambda e: (self.subject_of(e["courseid"], e.get("section")), e["filename"]))
            lines += ["", f"## {title}", ""]
            if entries:
                lines += [intro, ""] + table(header, [self.file_row(here, e, with_subject=True) for e in entries])
            else:
                lines.append(empty)
        return lines

    def mcc_page(self):
        lines = frontmatter({"type": "moodle-mcc", "tags": ["moodle"]})
        lines += [f"# {MCC}", "", f"Retour : {self.link(HOME, HOME)}", "",
                  "> [!important] Textes recopiés de Moodle (sections, étiquettes, pages) qui décrivent les contrôles "
                  "et le calcul des notes. Moodle reste la référence."]
        seen = set()
        for code in self.subjects:
            groups = [g for sha, g in self.mcc_groups(self.by_subject[code]["texts"]).items() if sha not in seen]
            if not groups:
                continue
            lines += ["", f"## {code}", "", self.subject_link(code)]
            for records in groups:
                first = records[0]
                seen.add(first["sha"])
                lines += ["", f"### {label(texts.heading(first))}", "",
                          " · ".join([web(first.get("url"), "Moodle"), texts.date_text(first)]), "", first["text"]]
        if not seen:
            lines += ["", "Aucun texte de Moodle ne décrit les modalités de contrôle."]
        return lines

    def grades_page(self):
        lines = frontmatter({"type": "moodle-notes", "tags": ["moodle"]})
        lines += [f"# {GRADES}", "", f"Retour : {self.link(HOME, HOME)}", "",
                  "> [!info] Notes publiées sur Moodle. Une nouvelle note reste un mystère jusqu'au clic sur "
                  "« Découvrir » dans l'application de barre des menus : sa valeur n'apparaît ici qu'ensuite."]
        summary = []
        for code, entries in self.grades.items():
            graded = sum(1 for e in entries if e.get("status") in ("revealed", "known"))
            hidden = sum(1 for e in entries if e.get("status") == "mystery")
            summary.append([self.subject_link(code, True), str(graded), str(hidden), str(len(entries) - graded - hidden)])
        lines += ["", "## Récapitulatif", ""]
        lines += table(["Matière", "Notées", "À découvrir", "Pas encore notées"], summary) or [
            "Aucun élément de note relevé pour l'instant."]
        for code, entries in self.grades.items():
            lines += ["", f"## {code}", ""]
            lines += table(["Élément", "Note", "Sur 20", "Relevée le", "Classeur"],
                           self.grade_rows(entries, with_workbook=True))
        return lines

    def subject_title(self, code):
        subject = self.subjects[code]
        name = subject["course"].get("fullname") or subject["course"].get("shortname") or code
        return f"partie de « {name} »" if subject["part"] else name

    def subject_page(self, code):
        subject = self.subjects[code]
        course = subject["course"]
        here = f"{subject['note']}.md"
        data = self.by_subject[code]
        deadlines = [r for r in self.deadlines if r["subject"] == code]
        documents = sum(1 for e in data["files"] if e.get("status") in LOCAL)
        props = {"type": "moodle-matiere", "code": code,
                 "aliases": [] if subject["part"] else [course.get("fullname")],
                 "cours_moodle": course.get("fullname"), "moodle": course.get("url"), "dossier": subject["dest"],
                 "documents": documents,
                 "devoirs_a_rendre": sum(1 for a in data["assignments"] if (a.get("duedate") or 0) >= self.now),
                 "prochaine_echeance": iso(deadlines[0]["at"]) if deadlines else None, "tags": ["moodle/matiere"]}
        lines = frontmatter(props)
        lines += [f"# {code} — {label(self.subject_title(code))}", "",
                  " · ".join([web(course.get("url"), "Ouvrir sur Moodle"),
                              f"[Dossier local]({self.cfg.resolve(subject['dest']).absolute().as_uri()})",
                              self.link(HOME, HOME)])]
        if subject["sections"]:
            lines += ["", f"Sections Moodle : {esc(', '.join(subject['sections']))}."]

        lines += ["", "## À venir", ""]
        lines += table(["Quand", "Quoi"], self.deadline_rows(deadlines, with_subject=False)) or ["Aucune échéance annoncée."]

        if data["announcements"]:
            lines += ["", "## Annonces récentes", ""]
            for ann in data["announcements"][:SUBJECT_ANNOUNCEMENTS]:
                lines += self.announcement(ann, with_subject=False)
            if len(data["announcements"]) > SUBJECT_ANNOUNCEMENTS:
                lines += ["", f"Toutes les annonces : {self.link(NEWS, NEWS)}."]

        mcc = self.mcc_groups(data["texts"])
        if mcc:
            lines += ["", "## Modalités de contrôle"]
            for records in mcc.values():
                first = records[0]
                lines += ["", *callout("important", label(texts.heading(first)), first["text"], folded=False),
                          ">", f"> {web(first.get('url'), 'Moodle')} · {texts.date_text(first)}"]

        assigns = sorted(data["assignments"], key=lambda a: (not a.get("duedate"), a.get("duedate") or 0))
        if assigns:
            lines += ["", "## Devoirs", ""]
            lines += table(["Devoir", "Date limite", "Statut"],
                           [[self.link(self.assignment_notes[a["cmid"]], a["name"], True),
                             when(a["duedate"]) if a.get("duedate") else "—", self.assignment_status(a)]
                            for a in assigns if a.get("cmid") in self.assignment_notes])

        if self.grades and self.grades.get(code):
            lines += ["", "## Notes", ""]
            lines += table(["Élément", "Note", "Sur 20", "Relevée le"], self.grade_rows(self.grades[code]))
            lines += ["", f"Toutes les notes : {self.link(GRADES, GRADES)}."]

        lines += ["", "## Contenu"]
        order = [s for s in course.get("sections", []) if self.subject_of(course["id"], s) == code]
        by_section = OrderedDict((s, {"files": [], "texts": [], "assignments": []}) for s in order)
        for kind in ("files", "texts", "assignments"):
            for value in data[kind]:
                if kind == "texts" and value["kind"] == "assign":
                    continue
                by_section.setdefault(value.get("section") or "Sans section",
                                      {"files": [], "texts": [], "assignments": []})[kind].append(value)
        for section, content in by_section.items():
            if not any(content.values()):
                continue
            lines += ["", f"### {label(section)}"]
            for record in content["texts"]:
                flag = "⚖️ " if texts.is_mcc(record, self.mcc_settings) else ""
                title = {"section": "Présentation de la section", "page": f"📄 Page : {record.get('title') or ''}",
                         "label": f"🏷️ {record.get('title') or 'Étiquette'}"}.get(
                    record["kind"], f"ℹ️ {record.get('title') or ''}")
                lines += ["", *callout("note", label(flag + title), record["text"])]
                if record.get("url") and record["kind"] != "section":
                    lines += [">", f"> {web(record['url'], 'Moodle')}"]
            if content["files"]:
                lines += [""] + table(["Document", "Taille", "Modifié", "Où"],
                                      [self.file_row(here, e) for e in content["files"]])
            for assign in content["assignments"]:
                if assign.get("cmid") not in self.assignment_notes:
                    continue
                due = f"à rendre le {when(assign['duedate'])}" if assign.get("duedate") else "sans date limite"
                lines += ["", f"- 📝 Devoir : {self.link(self.assignment_notes[assign['cmid']], assign['name'])} — {due}"]
        return lines

    def assignment_page(self, assign):
        code = self.subject_of(assign["courseid"], assign.get("section"))
        subject = self.subjects[code]
        here = f"{self.assignment_notes[assign['cmid']]}.md"
        due, cutoff = assign.get("duedate") or 0, assign.get("cutoffdate") or 0
        lines = frontmatter({"type": "moodle-devoir", "matiere": f"[[{self.prefix}{subject['note']}|{code}]]",
                             "date_limite": iso(due), "date_fermeture": iso(cutoff) if cutoff and cutoff != due else None,
                             "statut": self.assignment_status(assign), "moodle": assign.get("url"),
                             "tags": ["moodle/devoir"]})
        lines += [f"# {label(assign['name'])}", "",
                  " · ".join([self.link(subject["note"], code), web(assign.get("url"), "Ouvrir sur Moodle (dépôt)")]), ""]
        if due:
            lines.append(f"**Date limite** : {when(due, year=True)}")
        else:
            lines.append("**Sans date limite.**")
        if cutoff and cutoff != due:
            lines += ["", f"**Fermeture du dépôt** : {when(cutoff, year=True)}"]
        if assign.get("section"):
            lines += ["", f"Section Moodle : {esc(assign['section'])}."]
        record = self.instructions.get(assign.get("cmid"))
        lines += ["", "## Consignes", ""]
        lines += [record["text"], "", f"*{texts.date_text(record)}*"] if record and record.get("text") else [
            "Pas de consignes écrites sur Moodle."]
        files = [e for e in self.by_subject[code]["files"] if e.get("cmid") == assign.get("cmid")]
        if files:
            lines += ["", "## Pièces jointes", ""] + table(["Document", "Taille", "Modifié", "Où"],
                                                          [self.file_row(here, e) for e in files])
        return lines

    def render(self):
        """{chemin relatif au dossier du coffre : contenu} de toutes les notes générées."""
        builders = {HOME: self.home, DEADLINES: self.deadlines_page, NEWS: self.news_page,
                    DOCUMENTS: self.documents_page, MCC: self.mcc_page, GRADES: self.grades_page}
        pages = {f"{name}.md": builders[name]() for name in self.pages}
        for code, subject in list(self.subjects.items()):
            pages[f"{subject['note']}.md"] = self.subject_page(code)
        for assign in self.remote.get("assignments", []):
            if assign.get("cmid") in self.assignment_notes:
                pages[f"{self.assignment_notes[assign['cmid']]}.md"] = self.assignment_page(assign)
        return {util.nfc(path): "\n".join(lines).rstrip() + "\n" for path, lines in pages.items()}


def _generated(path):
    try:
        return MARKER in path.read_text("utf-8")[:2000]
    except OSError:
        return False


def write(cfg, state, now=None):
    """Écrit le coffre ; renvoie les notes réécrites. Une note dont le contenu ne change pas n'est pas touchée."""
    if not cfg.vault_dir:
        return []
    pages = Vault(cfg, state, now).render()
    directory = Path(cfg.vault_dir)
    written = []
    for rel, content in pages.items():
        path = directory / rel
        try:
            unchanged = path.read_text("utf-8") == content
        except OSError:
            unchanged = False
        if not unchanged:
            path.parent.mkdir(parents=True, exist_ok=True)
            util.write_text_atomic(path, content)
            written.append(path)
    # Notes générées devenues sans objet (devoir supprimé, matière retirée, notes désactivées)
    candidates = [directory / f"{name}.md" for name in (HOME, DEADLINES, NEWS, DOCUMENTS, MCC, GRADES)]
    for folder in (SUBJECTS, ASSIGNMENTS):
        candidates += sorted((directory / folder).glob("*.md")) if (directory / folder).is_dir() else []
    for path in candidates:
        rel = util.nfc(path.relative_to(directory).as_posix())
        if rel not in pages and path.exists() and _generated(path):
            path.unlink()
    return written
