"""Textes de Moodle : résumés de sections, étiquettes, pages, consignes des devoirs et annonces.

Le HTML renvoyé par l'API est converti en Markdown sobre (compatible Obsidian), conservé dans l'état
(`remote.texts`) et, si un dossier est configuré, recopié dans un fichier Markdown par cours. Les
modalités de contrôle des connaissances (formules de calcul des notes) sont repérées pour être mises en
évidence dans l'index.
"""

import hashlib
import os
import re
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

from . import util

MARKER = "genere_par: moodle-bridge"
# Types de textes susceptibles de décrire les modalités de contrôle (ni les annonces, ni les devoirs)
MCC_KINDS = ("section", "label", "description", "page")
KIND_LABELS = {"section": "texte de section", "label": "étiquette", "description": "description d'activité",
               "page": "page", "assign": "consignes de devoir", "announcement": "annonce"}

BOLD, ITALIC, QUOTE = "\x01", "\x02", "\x03"
BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "center",
              "address", "figure", "figcaption", "dl", "dt", "dd", "form", "fieldset", "details", "summary",
              "h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template", "svg", "math"}
TOKEN_PARAM = re.compile(r"([?&])(?:ws)?token=[^&#\s]*&?", re.IGNORECASE)


# ---------------------------------------------------------------- HTML → Markdown

class _NL:
    """Saut de ligne en attente : n retours, puis l'indentation de la ligne suivante."""
    __slots__ = ("n", "indent")

    def __init__(self, n, indent=""):
        self.n, self.indent = n, indent


def _render(parts):
    out, pending = [], None
    for part in parts:
        if isinstance(part, _NL):
            if pending is None:
                pending = part
            else:
                # Un paragraphe ouvert juste après « - » garde la puce de l'élément de liste
                keep = part.n == 1 and not part.indent.strip() and pending.indent.strip()
                pending = _NL(max(pending.n, part.n), pending.indent if keep else part.indent)
            continue
        if not part:
            continue
        if pending is not None:
            if out:
                out[-1] = out[-1].rstrip(" ")
                out.append("\n" * pending.n)
            out.append(pending.indent)
            part = part.lstrip(" ")
            pending = None
            if not part:
                continue
        out.append(part)
    return "".join(out)


def clean_url(url):
    """Lien ouvrable dans un navigateur, sans jeton ni caractère qui casse le Markdown."""
    url = (url or "").strip()
    if not url or url.startswith(("javascript:", "@@PLUGINFILE@@", "#")):
        return ""
    url = url.replace("/webservice/pluginfile.php", "/pluginfile.php")
    url = TOKEN_PARAM.sub(r"\1", url).rstrip("?&")
    url = re.sub(r"[?&]forcedownload=1", "", url)
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29").replace("<", "%3C").replace(">", "%3E")


def escape(text):
    """Échappe ce que Markdown ou Obsidian interpréteraient dans un texte ordinaire."""
    text = text.replace("\\", "\\\\")
    for char in "*`[]$<":
        text = text.replace(char, "\\" + char)
    return re.sub(r"#(?=\S)", r"\\#", text)  # « #mot » deviendrait une étiquette Obsidian


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []      # [balise, compteur]
        self.marks = []      # (balise, position de départ, données)
        self.table = None    # lignes, chacune une liste de cellules
        self.bold = self.italic = self.skip = self.pre = self.code = 0

    # -- blocs et listes
    def _indent(self, item=False):
        depth = max(len(self.lists) - 1, 0)
        if not self.lists:
            return ""
        return "    " * depth + ("" if item else "  ")

    def _block(self):
        self.parts.append(_NL(1, self._indent()) if self.lists else _NL(2))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip += 1
        if self.skip:
            return
        if self.pre:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag in BLOCK_TAGS:
            self._block()
            if tag[0] == "h" and tag[1:].isdigit():
                self._bold(+1)
        elif tag == "br":
            self.parts.append(_NL(1, self._indent()))
        elif tag in ("ul", "ol"):
            if not self.lists:
                self.parts.append(_NL(2))
            self.lists.append([tag, 0])
        elif tag == "li":
            if not self.lists:
                self.lists.append(["ul", 0])
            self.lists[-1][1] += 1
            kind, count = self.lists[-1]
            self.parts.append(_NL(1, self._indent(item=True) + (f"{count}. " if kind == "ol" else "- ")))
        elif tag in ("strong", "b"):
            self._bold(+1)
        elif tag in ("em", "i"):
            self.italic += 1
            if self.italic == 1:
                self.parts.append(ITALIC)
        elif tag == "a":
            self.marks.append(("a", len(self.parts), clean_url(attrs.get("href"))))
        elif tag == "code":
            self.code += 1
            self.marks.append(("code", len(self.parts), None))
        elif tag == "pre":
            self.pre += 1
            self.marks.append(("pre", len(self.parts), None))
        elif tag == "blockquote":
            self.parts.append(_NL(2))
            self.marks.append(("blockquote", len(self.parts), None))
        elif tag == "hr":
            self.parts += [_NL(2), "* * *", _NL(2)]
        elif tag == "img":
            url, alt = clean_url(attrs.get("src")), (attrs.get("alt") or "").strip()
            label = escape(alt) if alt else "image"
            self.parts.append(f"[{label}]({url})" if url else f"({label})")
        elif tag in ("iframe", "embed", "source", "video", "audio") and attrs.get("src"):
            url = clean_url(attrs.get("src"))
            if url:
                self.parts.append(f" [contenu intégré]({url}) ")
        elif tag == "table":
            self.parts.append(_NL(2))
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.marks.append(("cell", len(self.parts), None))

    def _bold(self, step):
        if step > 0:
            self.bold += 1
            if self.bold == 1:
                self.parts.append(BOLD)
        elif self.bold:
            if self.bold == 1:
                self.parts.append(BOLD)
            self.bold -= 1

    def _pop(self, kind):
        for index in range(len(self.marks) - 1, -1, -1):
            if self.marks[index][0] == kind:
                _, start, data = self.marks.pop(index)
                inner = _render(self.parts[start:])
                del self.parts[start:]
                return inner, data
        return None, None

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(self.skip - 1, 0)
            return
        if self.skip:
            return
        if self.pre and tag != "pre":
            return
        if tag in BLOCK_TAGS:
            if tag[0] == "h" and tag[1:].isdigit():
                self._bold(-1)
            self._block()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.parts.append(_NL(1, self._indent()) if self.lists else _NL(2))
        elif tag in ("strong", "b"):
            self._bold(-1)
        elif tag in ("em", "i") and self.italic:
            if self.italic == 1:
                self.parts.append(ITALIC)
            self.italic -= 1
        elif tag == "a":
            inner, url = self._pop("a")
            if inner is None:
                return
            label = " ".join(inner.split())
            if not url:
                self.parts.append(label)
            elif not label.replace(BOLD, "").replace(ITALIC, "").strip() or label == escape(url):
                self.parts.append(f"<{url}>")
            else:
                self.parts.append(f"[{label}]({url})")
        elif tag == "code" and self.code:
            self.code -= 1
            inner, _ = self._pop("code")
            if inner and inner.strip():
                fence = "``" if "`" in inner else "`"
                self.parts.append(f"{fence}{inner.strip()}{fence}")
        elif tag == "pre" and self.pre:
            self.pre -= 1
            inner, _ = self._pop("pre")
            if inner and inner.strip():
                self.parts += [_NL(2), "```\n" + inner.strip("\n") + "\n```", _NL(2)]
        elif tag == "blockquote":
            inner, _ = self._pop("blockquote")
            if inner and inner.strip():
                self.parts += ["\n".join(QUOTE + " " + line for line in inner.strip().split("\n")), _NL(2)]
        elif tag in ("td", "th") and self.table is not None:
            inner, _ = self._pop("cell")
            if self.table:
                self.table[-1].append(" ".join((inner or "").split()).replace("|", "\\|"))
        elif tag == "table" and self.table is not None:
            rows = [row for row in self.table if any(cell for cell in row)]
            self.table = None
            if rows:
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
                lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
                self.parts += ["\n".join(lines), _NL(2)]

    def handle_data(self, data):
        if self.skip:
            return
        data = re.sub(r"[\x00-\x08​﻿]", "", data)
        if self.pre:
            self.parts.append(data.replace("\r", ""))
        elif self.code:
            self.parts.append(re.sub(r"\s+", " ", data))
        else:
            self.parts.append(escape(re.sub(r"\s+", " ", data)))

    def result(self):
        while self.bold:
            self._bold(-1)
        if self.italic:
            self.parts.append(ITALIC)
        return _render(self.parts)


def _emphasis(text, mark, symbol):
    """Remplace les marqueurs internes par ** ou *, ligne par ligne et sans espace collé au symbole."""
    pieces = text.split(mark)
    if len(pieces) % 2 == 0:  # marqueur orphelin : ignoré
        pieces[-2:] = [pieces[-2] + pieces[-1]]
    for index in range(1, len(pieces), 2):
        lines = []
        for line in pieces[index].split("\n"):
            match = re.match(r"^(\s*(?:\x03\s*)*(?:[-+]\s+|\d+\.\s+)?)(\s*)(.*?)(\s*)$", line)
            prefix, lead, core, trail = match.groups()
            lines.append(f"{prefix}{lead}{symbol}{core}{symbol}{trail}" if core else line)
        pieces[index] = "\n".join(lines)
    return "".join(pieces)


def html_to_md(source):
    """Markdown lisible à partir du HTML d'un texte Moodle ; chaîne vide si le texte est vide."""
    if not source or not source.strip():
        return ""
    converter = _Converter()
    converter.feed(source)
    converter.close()
    text = converter.result()
    text = _emphasis(text, BOLD, "**")
    text = _emphasis(text, ITALIC, "*")
    text = text.replace("****", "").replace(BOLD, "").replace(ITALIC, "")
    lines, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip(QUOTE + " ").startswith("```"):
            fenced = not fenced
        elif not fenced:
            line = line.rstrip()
            # Début de ligne qui deviendrait un titre ou une citation
            line = re.sub(r"^([\s\x03]*)([#>]|[-=]{2,}\s*$)", r"\1\\\2", line)
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n").replace(QUOTE, ">")
    return TOKEN_PARAM.sub(r"\1", text).strip()


# ---------------------------------------------------------------- état

def digest(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def merge(previous, fetched, keep, present_courses, known_courses, failed_kinds, now):
    """Fusionne les textes relevés avec ceux de l'état ; renvoie (textes, identifiants modifiés).

    - `changed_at` n'est posé que pour un changement constaté par le pont : les textes d'un cours relevé
      pour la première fois ne comptent pas comme modifiés ;
    - les annonces plus anciennes que la fenêtre interrogée sont conservées ;
    - les textes d'un cours absent de Moodle (masqué, inscription expirée), ou d'un type que Moodle n'a
      pas pu fournir à ce passage, sont conservés tels quels.
    """
    texts, changed = {}, []
    oldest = {}
    for record in fetched.values():
        if record["kind"] == "announcement":
            oldest[record["courseid"]] = min(oldest.get(record["courseid"], record["time"]), record["time"])
    for tid, record in fetched.items():
        if not keep(record["courseid"]) or not record.get("text"):
            continue
        old = previous.get(tid) or {}
        entry = {**record, "sha": digest(record["text"]), "seen_at": old.get("seen_at") or now,
                 "changed_at": old.get("changed_at")}
        if (old and old.get("sha") != entry["sha"]) or (not old and record["courseid"] in known_courses):
            entry["changed_at"] = now
            changed.append(tid)
        texts[tid] = entry
    for tid, old in previous.items():
        if tid in texts or not keep(old["courseid"]):
            continue
        if (old["courseid"] not in present_courses or old["kind"] in failed_kinds
                or old["kind"] == "announcement" and (old.get("time") or 0) < oldest.get(old["courseid"], float("inf"))):
            texts[tid] = old
    return texts, changed


def is_mcc(record, settings):
    """Texte décrivant les modalités de contrôle : repéré par son titre ou par une formule de calcul."""
    if record.get("kind") not in MCC_KINDS:
        return False
    title = f"{record.get('section') or ''} {record.get('title') or ''}"
    return bool(re.search(settings.get("mcc_title") or "$^", title, re.IGNORECASE)
                or re.search(settings.get("mcc_content") or "$^", record.get("text") or "", re.IGNORECASE))


def ordered(texts, courses):
    """Textes dans l'ordre de Moodle : cours, section, position dans la section ; annonces à la fin."""
    rank = {c["id"]: i for i, c in enumerate(courses)}
    return sorted(texts.values(), key=lambda t: (
        rank.get(t["courseid"], len(rank)), t["courseid"], t["kind"] == "announcement",
        999 if t.get("section_pos") is None else t["section_pos"],  # devoir ou page hors des sections
        -1 if t.get("position") is None else t["position"],
        -(t.get("time") or 0) if t["kind"] == "announcement" else 0,
        t.get("title") or ""))


def heading(record):
    if record["kind"] == "section" or not record.get("title"):
        return record.get("section") or ""
    return f"{record['section']} › {record['title']}" if record.get("section") else record["title"]


def date_text(record):
    if record["kind"] == "announcement":
        text = f"publiée le {util.fmt_date(record.get('time'), with_time=True)}"
        if (record.get("modified") or 0) > (record.get("time") or 0) + 60:
            text += f", modifiée le {util.fmt_date(record['modified'], with_time=True)}"
        return text
    if record.get("time"):
        return f"modifié sur Moodle le {util.fmt_date(record['time'])}"
    if record.get("changed_at"):
        return f"modifié le {util.fmt_date(record['changed_at'])} (constaté par le pont)"
    return f"relevé le {util.fmt_date(record.get('seen_at'))}"


# ---------------------------------------------------------------- fichiers par cours

def course_file(cfg, courseid):
    directory, conf = cfg.texts_dir, cfg.course(courseid)
    if not directory or not conf:
        return None
    return directory / f"{util.safe_name(conf['code'], 60)}.md"


def _link(url, label):
    return f"[{label}]({url})" if url else label


def render_course(cfg, course, records):
    """Fichier Markdown d'un cours : textes section par section, devoirs, puis annonces."""
    conf = cfg.course(course["id"])
    settings = cfg.data["texts"]
    lines = ["---", "type: textes_moodle", f"cours: {conf['code']}", f"source: {course.get('url') or cfg.site}",
             MARKER, "---", "", f"# {conf['code']} — {course.get('fullname') or course.get('shortname') or ''}", "",
             "> [!info] Textes copiés de Moodle par moodle-bridge : résumés de sections, étiquettes, pages, "
             "consignes des devoirs et annonces. Ne pas modifier : ce fichier est réécrit quand Moodle change.",
             "> ⚖️ = modalités de contrôle des connaissances."]
    current = None
    announcements = []
    for record in records:
        if record["kind"] == "announcement":
            announcements.append(record)
            continue
        section = record.get("section") or "Sans section"
        if section != current:
            current = section
            lines += ["", f"## {section}"]
        flag = "⚖️ " if is_mcc(record, settings) else ""
        meta = " · ".join([_link(record.get("url"), "Moodle"), date_text(record)])
        if record["kind"] != "section":
            icon = {"page": "📄 Page", "assign": "📝 Devoir", "label": "🏷️ Étiquette"}.get(record["kind"], "ℹ️")
            title = f"{icon} : {record.get('title') or ''}"
            if record["kind"] == "assign" and record.get("duedate"):
                title += f" (à rendre le {util.fmt_date(record['duedate'], with_time=True)})"
            lines += ["", f"### {flag}{title}"]
            flag = ""
        lines += ["", f"{flag}{meta}", "", record["text"]]
    if announcements:
        lines += ["", "## Annonces"]
        for record in announcements:
            author = f" · {record['author']}" if record.get("author") else ""
            lines += ["", f"### {util.fmt_date(record.get('time'))} — {record.get('title') or ''}", "",
                      f"{_link(record.get('url'), 'Moodle')} · {date_text(record)}{author}", "", record["text"]]
    return "\n".join(lines).rstrip() + "\n"


def write_files(cfg, state):
    """Un fichier Markdown par cours, réécrit seulement s'il change ; renvoie les fichiers écrits."""
    directory = cfg.texts_dir
    remote = state.get("remote", {})
    texts = remote.get("texts") or {}
    if not directory or not texts:
        return []
    courses = {c["id"]: c for c in remote.get("courses", [])}
    by_course = {}
    for record in ordered(texts, remote.get("courses", [])):
        if cfg.course(record["courseid"]):
            by_course.setdefault(record["courseid"], []).append(record)
    written, expected = [], set()
    for courseid, records in by_course.items():
        course = courses.get(courseid) or {"id": courseid, "shortname": records[0].get("course"),
                                            "url": f"{cfg.site}/course/view.php?id={courseid}"}
        path = course_file(cfg, courseid)
        expected.add(path.name)
        content = render_course(cfg, course, records)
        try:
            unchanged = path.read_text("utf-8") == content
        except OSError:
            unchanged = False
        if not unchanged:
            util.write_text_atomic(path, content)
            written.append(path)
    # Fichier d'un cours retiré de la configuration : supprimé seulement s'il a été généré par le pont
    for path in directory.glob("*.md"):
        try:
            if path.name not in expected and MARKER in path.read_text("utf-8")[:500]:
                path.unlink()
        except OSError:
            pass
    return written


def link_from(index_path, target):
    """Lien Markdown vers `target` depuis l'index : relatif s'il est sous le dossier de l'index (Obsidian ou
    VS Code l'ouvrent alors eux-mêmes), sinon adresse file:// (ouverte par l'application du système)."""
    target = Path(target).absolute()
    if index_path:
        try:
            rel = Path(os.path.relpath(target, Path(index_path).absolute().parent)).as_posix()
            if not rel.startswith(".."):
                return urllib.parse.quote(rel)
        except ValueError:  # autre lecteur sous Windows
            pass
    return target.as_uri()
