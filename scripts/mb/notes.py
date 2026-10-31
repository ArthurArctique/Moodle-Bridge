"""Notes Moodle : relevé fréquent et déterministe, notification mystère, révélation, report dans le classeur.

Indépendant de la synchronisation des documents : sa propre tâche launchd (toutes les 15 min par défaut), son état
(<env>/.moodle-bridge/notes-state.json), son verrou et son journal. Un passage sans nouvelle note n'appelle aucun
modèle ; quand une note apparaît, un modèle choisit l'emoji, la couleur et l'accroche qui l'évoquent.

Tant qu'une note est un « mystère », sa valeur n'est ni dans la notification, ni dans le journal, ni dans la vue de
l'application (~/Library/Caches/moodle-bridge/<slug>/notes.json). L'application reçoit seulement une enveloppe
scellée (notes-sealed.json, encodée en base64 pour qu'aucune valeur ne s'affiche par mégarde), qu'elle n'ouvre qu'au
clic sur « Découvrir » : la note apparaît aussitôt, sans attendre la tâche. L'application dépose en même temps une
demande dans notes-requests/ et relance la tâche, qui enregistre la découverte, puis reporte la note dans le
classeur si l'élément Moodle y est relié (`notes.workbook.map`), par notes_workbook.py et openpyxl.
"""

import base64
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from . import classify, index, llm, schedule, sync, system, texts, util
from .api import MoodleError, TokenError

VERSION = 1
MAX_VIEW = 40
MAX_FEEDBACK = 600
MAX_HISTORY = 50
MAX_TEASER = 60
MYSTERY_ATTEMPTS = 3
NOTIFY_ONE_BY_ONE = 3
VENV = system.data_root() / "venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if system.IS_WINDOWS else "bin/python3")
OPENPYXL = "openpyxl==3.1.5"
WRITER = Path(__file__).resolve().parents[1] / "notes_workbook.py"
# Report terminé (ou impossible par nature) : on ne réessaie plus
FINAL_WORKBOOK = {"written", "unchanged", "not_numeric", "disabled"}
DOT_COLORS = {"🔴": "#E5484D", "🟠": "#F76B15", "🟡": "#FFC53D", "🟢": "#30A46C", "🔵": "#0090FF",
              "🟣": "#8E4EC6", "🟤": "#A07553", "⚫": "#3A3A3C", "⚪": "#C7C7CC"}
FALLBACK = {"emoji": "🎁", "color": "#8E8E93", "dot": "⚪", "teaser": None, "by": None}
HEX = re.compile(r"#[0-9A-Fa-f]{6}")
MYSTERY_SYSTEM = (
    "Tu es un module appelé par un script, sans humain dans la boucle. Tu ne converses pas et tu ne poses "
    "jamais de question : tu réponds uniquement par l'objet JSON demandé, sans texte autour ni balises de code."
)


# ---------------------------------------------------------------- chemins et état

def settings(cfg):
    return cfg.data["notes"]


def state_path(cfg):
    return cfg.dir / "notes-state.json"


def lock_path(cfg):
    return cfg.dir / "notes.lock"


def log_path(cfg):
    return cfg.dir / "logs" / "notes.log"


def view_path(cfg):
    return cfg.cache_dir / "notes.json"


def sealed_path(cfg):
    return cfg.cache_dir / "notes-sealed.json"


def requests_dir(cfg):
    return cfg.cache_dir / "notes-requests"


def workbook_path(cfg):
    rel = settings(cfg)["workbook"].get("path")
    return Path(os.path.normpath(cfg.resolve(rel))) if rel else None


def workbook_python(cfg):
    return Path(settings(cfg)["workbook"].get("python") or VENV_PYTHON).expanduser()


def empty_state():
    return {"version": 1, "userid": None, "initialized": False, "items": {}, "history": [],
            "last_check": None, "last_attempt": None, "last_error": None, "last_error_kind": None}


def load_state(cfg):
    path = state_path(cfg)
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text("utf-8"))
    for key, value in empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(cfg, state):
    state["history"] = state["history"][-MAX_HISTORY:]
    util.write_json_atomic(state_path(cfg), state)


# ---------------------------------------------------------------- relevé sur Moodle

def item_name(item):
    return util.nfc(html.unescape(item.get("itemname") or "")).strip() or "Élément sans nom"


def tracked(item, conf):
    """Élément de note réel : ni total de cours ou de catégorie, ni activité exclue (quiz d'entraînement…)."""
    if item.get("itemtype") in ("course", "category"):
        return False
    name = item_name(item)
    if any(re.search(p, name, re.IGNORECASE) for p in conf.get("include_items") or []):
        return True
    if item.get("itemmodule") in (conf.get("exclude_modules") or []):
        return False
    return not any(re.search(p, name, re.IGNORECASE) for p in conf.get("exclude_items") or [])


def _number(value):
    try:
        return None if value is None else round(float(value), 5)
    except (TypeError, ValueError):
        return None


def grade_of(item):
    """Note d'un élément, ou None tant qu'il n'est pas noté (ou que la note reste masquée à l'étudiant)."""
    raw = _number(item.get("graderaw"))
    shown = (item.get("gradeformatted") or "").strip()
    if raw is None and shown in ("", "-"):
        return None
    feedback = texts.html_to_md(item["feedback"]).strip() if item.get("feedback") else ""
    return {"raw": raw, "formatted": shown or None, "min": _number(item.get("grademin")) or 0.0,
            "max": _number(item.get("grademax")), "scale": bool(item.get("scaleid")),
            "percentage": (item.get("percentageformatted") or "").strip() or None,
            "feedback": feedback[:MAX_FEEDBACK] or None, "dategraded": item.get("gradedategraded")}


def signature(grade):
    """Ce qui fait qu'une note a changé : la valeur et le barème, pas le commentaire ni la mise en forme."""
    if grade is None:
        return None
    return [grade["raw"], grade["max"]] if grade["raw"] is not None else [grade["formatted"]]


def fetch(cfg, client, state):
    """Éléments de note suivis de tous les cours : ({clé: infos}, cours relevés, erreurs par cours)."""
    conf = settings(cfg)
    if not state.get("userid"):
        state["userid"] = client.call("core_webservice_get_site_info")["userid"]
    userid = state["userid"]
    found, checked, errors = {}, set(), []
    for course in client.call("core_enrol_get_users_courses", userid=userid):
        courseid = course["id"]
        if cfg.is_skipped(courseid):
            continue
        try:
            report = client.call("gradereport_user_get_grade_items", courseid=courseid, userid=userid)
        except TokenError:
            raise
        except MoodleError as exc:
            errors.append(f"{course.get('shortname') or courseid} : {exc}")
            continue
        checked.add(courseid)
        code = index.course_code(cfg, courseid, course.get("shortname") or str(courseid))
        for usergrade in (report.get("usergrades") or [])[:1]:
            for item in usergrade.get("gradeitems") or []:
                if not tracked(item, conf):
                    continue
                found[f"{courseid}/{item['id']}"] = {
                    "courseid": courseid, "itemid": item["id"], "code": code,
                    "course": util.nfc(course.get("fullname") or ""), "name": item_name(item),
                    "module": item.get("itemmodule"), "itemtype": item.get("itemtype"),
                    "url": f"{cfg.site}/grade/report/user/index.php?id={courseid}",
                    "grade": grade_of(item),
                }
    return found, checked, errors


def merge(state, found, checked, now_iso):
    """Met l'état à jour ; renvoie les clés des notes nouvelles ou modifiées, qui deviennent des mystères.

    Au tout premier relevé, les notes déjà présentes sont seulement mémorisées (« connues ») : seules celles qui
    apparaissent ensuite sont annoncées.
    """
    items = state["items"]
    initial = not state.get("initialized")
    fresh = []
    for key, info in found.items():
        info = dict(info)
        grade = info.pop("grade")
        entry = items.setdefault(key, {"status": "none", "grade": None})
        entry.update(info)
        new_sig = signature(grade)
        if new_sig is None:
            # Note retirée ou de nouveau masquée : un mystère pas encore ouvert disparaît, une note découverte reste
            if entry["status"] == "mystery":
                entry.update(status="none", grade=None, mystery=None)
            continue
        if new_sig == signature(entry.get("grade")):
            entry["grade"] = grade  # commentaire ou pourcentage mis à jour
            continue
        if initial:
            entry.update(status="known", grade=grade)
            continue
        if entry["status"] in ("revealed", "known"):
            entry.update(event="changed", previous=entry.get("grade"))
        elif entry["status"] != "mystery":
            entry.update(event="new", previous=None)
        entry.update(status="mystery", grade=grade, detected_at=now_iso, revealed_at=None, mystery=None,
                     workbook=None)
        fresh.append(key)
    # Élément supprimé de Moodle (dans un cours bien relevé) : on oublie ce qui n'a jamais été découvert
    for key in [k for k, e in items.items() if k not in found and e.get("courseid") in checked
                and e.get("status") in ("none", "mystery")]:
        del items[key]
    if checked:
        state["initialized"] = True
    return fresh


# ---------------------------------------------------------------- mystère (modèle de langage)

def mystery_engine(cfg):
    conf = settings(cfg)["mystery"]
    if not conf.get("enabled"):
        return None
    return llm.make_engine(llm.settings_for(conf, cfg.data["classify"]), MYSTERY_SYSTEM)


def fr_number(value):
    if value is None:
        return "—"
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def on20(grade):
    """Note ramenée sur 20 (None pour un barème non numérique)."""
    if not grade or grade.get("scale") or grade.get("raw") is None or not grade.get("max"):
        return None
    low = grade.get("min") or 0.0
    if grade["max"] <= low:
        return None
    return round((grade["raw"] - low) / (grade["max"] - low) * 20, 2)


def display(grade):
    """« 15,5 / 20 », ou la note mise en forme par Moodle pour un barème non numérique."""
    if not grade:
        return None
    if grade.get("raw") is None or grade.get("scale") or grade.get("max") is None:
        return grade.get("formatted") or "—"
    return f"{fr_number(grade['raw'])} / {fr_number(grade['max'])}"


def build_mystery_prompt(rows):
    lines = []
    for number, entry in rows:
        grade = entry["grade"]
        value = display(grade)
        if on20(grade) is not None and grade.get("max") != 20:
            value += f" (soit {fr_number(on20(grade))} / 20)"
        lines.append(f"- id={number} | {entry['code']} · {entry['name']} | note : {value}")
    return f"""Un étudiant vient de recevoir des notes sur Moodle et ne les a pas encore vues. Il va recevoir
une notification « mystère » puis découvrira chaque note d'un clic. Pour chaque note, choisis librement ce qui
l'évoque le mieux, à ton intuition, sans barème fixe :
- "emoji" : UN seul emoji qui suggère la note sans la dire (ni chiffre, ni lettre, ni touche numérotée) ;
- "color" : une couleur hexadécimale #RRGGBB qui représente cette note ;
- "dot" : la pastille la plus proche de cette couleur, parmi {" ".join(DOT_COLORS)} ;
- "teaser" : une phrase courte et intrigante ({MAX_TEASER - 15} caractères au plus) qui donne envie de cliquer,
  sans aucun chiffre et sans dire la note.

Réponds UNIQUEMENT par :
{{"mysteries": [{{"id": "<id fourni>", "emoji": "…", "color": "#RRGGBB", "dot": "…", "teaser": "…"}}]}}

Notes :
{chr(10).join(lines)}
"""


def parse_mysteries(text):
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise classify.ClassifierError("réponse du modèle illisible")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise classify.ClassifierError("réponse du modèle illisible") from exc
    rows = data.get("mysteries") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and "id" in r]


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def nearest_dot(color):
    target = _rgb(color)
    return min(DOT_COLORS, key=lambda dot: sum((a - b) ** 2 for a, b in zip(_rgb(DOT_COLORS[dot]), target)))


def _speaks(text):
    """Vrai si le texte contient une lettre ou un chiffre (un emoji ne doit rien écrire, une accroche aucun chiffre)."""
    return any(unicodedata.category(c)[0] in "LN" for c in text)


def clean_mystery(answer):
    """Choix du modèle validé champ par champ ; None si l'emoji est inutilisable."""
    emoji = str(answer.get("emoji") or "").strip()
    if not emoji or len(emoji) > 12 or _speaks(emoji) or any(c.isspace() for c in emoji):
        return None
    color = str(answer.get("color") or "").strip()
    dot = str(answer.get("dot") or "").strip()
    color = color.upper() if HEX.fullmatch(color) else None
    if dot not in DOT_COLORS:
        dot = nearest_dot(color) if color else FALLBACK["dot"]
    teaser = " ".join(str(answer.get("teaser") or "").split())
    if not teaser or len(teaser) > MAX_TEASER or any(unicodedata.category(c)[0] == "N" for c in teaser):
        teaser = None
    return {"emoji": emoji, "color": color or DOT_COLORS[dot], "dot": dot, "teaser": teaser}


def make_mysteries(state, keys, engine, log):
    """Emoji, couleur et accroche des notes données ; 🎁 gris si le modèle échoue (nouvel essai au passage suivant)."""
    rows = [(str(n), state["items"][k]) for n, k in enumerate(keys, start=1)]
    chosen, error = {}, None
    if engine and rows:
        try:
            for answer in parse_mysteries(engine.ask(build_mystery_prompt(rows))):
                mystery = clean_mystery(answer)
                if mystery:
                    chosen[str(answer["id"])] = mystery
        except classify.ClassifierError as exc:
            error = str(exc)
    model = ", ".join(sorted(getattr(engine, "models_used", ()) or ())) or getattr(engine, "model", None)
    for number, entry in rows:
        previous = entry.get("mystery") or {}
        if number in chosen:
            entry["mystery"] = {**chosen[number], "by": model, "attempts": previous.get("attempts", 0) + 1}
            log(f"mystère : {entry['code']} · {entry['name']} → {chosen[number]['emoji']} {chosen[number]['dot']}")
        else:
            entry["mystery"] = {**FALLBACK, "attempts": previous.get("attempts", 0) + (1 if engine else 0)}
            log(f"mystère par défaut : {entry['code']} · {entry['name']}"
                + (f" ({error})" if error else " (modèle désactivé)" if not engine else ""))
    return error


def notify_mysteries(entries):
    if len(entries) <= NOTIFY_ONE_BY_ONE:
        for entry in entries:
            mystery = entry["mystery"]
            what = "Note modifiée" if entry.get("event") == "changed" else "Nouvelle note"
            util.notify(f"{mystery['emoji']} {what} à découvrir", mystery.get("teaser") or "Ouvre Moodle Bridge pour la découvrir.",
                        subtitle=f"{mystery['dot']} {entry['code']} · {entry['name']}")
        return
    util.notify(f"🎁 {len(entries)} notes à découvrir", ", ".join(f"{e['code']} · {e['name']}" for e in entries),
                subtitle=" ".join(e["mystery"]["emoji"] for e in entries))


# ---------------------------------------------------------------- révélation et classeur

def take_requests(cfg, state, log):
    """Révélations demandées par l'application (un fichier par clic sur « Découvrir »)."""
    folder = requests_dir(cfg)
    revealed = []
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            key = json.loads(path.read_text("utf-8")).get("key")
        except (OSError, ValueError, AttributeError):
            key = None
        entry = state["items"].get(key) if isinstance(key, str) else None
        if entry and entry.get("status") == "mystery":
            entry.update(status="revealed", revealed_at=util.iso_now(), workbook={"status": "todo"})
            revealed.append(key)
            log(f"découverte : {entry['code']} · {entry['name']}")
        path.unlink(missing_ok=True)
    return revealed


def target_of(cfg, key):
    target = settings(cfg)["workbook"].get("map", {}).get(key)
    if isinstance(target, dict) and target.get("element") and target.get("note") in (1, 2, 3, 4):
        return str(target["element"]), int(target["note"])
    return None


def run_writer(python, request, timeout=120):
    try:
        proc = subprocess.run([str(python), "-I", str(WRITER)], input=json.dumps(request), capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "message": f"écriture impossible : {exc}"}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "error", "message": (proc.stderr.strip() or "réponse illisible")[-300:]}


def report_one(cfg, state, key, entry, runner):
    path = workbook_path(cfg)
    if not path:
        return {"status": "disabled"}
    target = target_of(cfg, key)
    if not target:
        return {"status": "unmapped"}
    value = on20(entry.get("grade"))
    if value is None:
        return {"status": "not_numeric"}
    element, note = target
    base = {"element": element, "note": note}
    for other_key, other in state["items"].items():
        written = other.get("written")
        if other_key != key and written and (written["element"], written["note"]) == target:
            return {**base, "status": "conflict", "message": f"case déjà remplie pour « {other['name']} »"}
    if path.with_name("~$" + path.name).exists():
        return {**base, "status": "locked"}
    python = workbook_python(cfg)
    if not python.exists():
        return {**base, "status": "error", "message": "openpyxl absent : lancer `notes setup`"}
    result = runner(python, {"path": str(path), "sheet": settings(cfg)["workbook"].get("sheet"),
                             "element": element, "note": note, "value": value})
    status = result.get("status") if result.get("status") in ("written", "unchanged", "locked", "busy") else "error"
    if status in ("written", "unchanged"):
        entry["written"] = {**base, "cell": result.get("cell"), "value": value}
    return {**base, "status": status, "cell": result.get("cell"), "previous": result.get("previous"),
            "backup": result.get("backup"), "message": result.get("message"), "at": util.iso_now()}


def sync_workbook(cfg, state, log, runner=run_writer):
    """Reporte les notes découvertes pas encore dans le classeur ; Excel ouvert ou relation manquante : plus tard."""
    for key, entry in state["items"].items():
        before = entry.get("workbook") or {}
        if entry.get("status") != "revealed" or before.get("status") in FINAL_WORKBOOK:
            continue
        after = report_one(cfg, state, key, entry, runner)
        # « en attente » ou « à relier » restent tels quels d'un passage à l'autre : ne le noter qu'une fois
        if (after["status"], after.get("message")) != (before.get("status"), before.get("message")):
            where = f"{after['element']} Note {after['note']}" if after.get("element") else "classeur"
            detail = {"written": f"écrite en {after.get('cell')}, sauvegarde "
                                 f"{Path(after['backup']).name if after.get('backup') else '?'}",
                      "unchanged": f"déjà présente en {after.get('cell')}"}.get(after["status"], after["status"])
            log(f"classeur : {entry['code']} · {entry['name']} → {where} : {detail}"
                + (f" ({after['message']})" if after.get("message") else ""))
        entry["workbook"] = after


# ---------------------------------------------------------------- vue pour l'application

def _ts(iso):
    return int(util.parse_iso(iso)) if iso else None


WORKBOOK_TONES = {"written": "ok", "unchanged": "ok", "todo": "wait", "locked": "wait", "busy": "wait",
                  "unmapped": "info", "not_numeric": "info", "conflict": "error", "error": "error"}


def workbook_label(wb):
    status = wb.get("status")
    where = f"{wb.get('element')}, Note {wb.get('note')}" + (f" ({wb['cell']})" if wb.get("cell") else "")
    previous = wb.get("previous")
    replaced = (f" — remplace {fr_number(previous) if isinstance(previous, (int, float)) else previous}"
                if previous not in (None, "") and status == "written" else "")
    return {
        "written": f"Reportée dans le classeur : {where}{replaced}",
        "unchanged": f"Déjà dans le classeur : {where}",
        "todo": "Report dans le classeur en cours…",
        "locked": "Classeur ouvert dans Excel : report au prochain passage",
        "busy": "Classeur modifié pendant l'écriture : nouvel essai au prochain passage",
        "unmapped": "Pas encore reliée au classeur : demander à l'agent de la relier",
        "not_numeric": "Note non numérique : pas de report dans le classeur",
        "conflict": f"Non reportée : {wb.get('message')}",
        "error": f"Report impossible : {wb.get('message')}",
    }.get(status)


def revealed_fields(entry, wb):
    """Ce que l'application affiche d'une note découverte."""
    grade = entry.get("grade") or {}
    score = on20(grade)
    return {"grade": display(grade), "on20": score if grade.get("max") != 20 else None,
            "ratio": (score / 20) if score is not None else None,
            "percentage": grade.get("percentage"), "feedback": grade.get("feedback"),
            "previous": display(entry.get("previous")),
            "workbook": workbook_label(wb), "workbook_tone": WORKBOOK_TONES.get(wb.get("status"))}


def expected_workbook(cfg, key, entry):
    """Sort prévisible du report, pour l'enveloppe : la tâche fera le vrai report après la découverte."""
    if not workbook_path(cfg):
        return {"status": "disabled"}
    if not target_of(cfg, key):
        return {"status": "unmapped"}
    if on20(entry.get("grade")) is None:
        return {"status": "not_numeric"}
    return {"status": "todo"}


def build_sealed(cfg, state, now=None):
    """Enveloppes des notes encore mystère, que l'application n'ouvre qu'au clic sur « Découvrir »."""
    sealed = {}
    for key, entry in state["items"].items():
        if entry.get("status") == "mystery":
            fields = revealed_fields(entry, expected_workbook(cfg, key, entry))
            sealed[key] = base64.b64encode(json.dumps(fields, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return {"version": VERSION, "generated_at": int(now or time.time()), "sealed": sealed}


def build_view(cfg, state, running=False, now=None):
    now = int(now or time.time())
    conf = settings(cfg)
    rows = []
    for key, entry in state["items"].items():
        if entry.get("status") not in ("mystery", "revealed"):
            continue
        mystery = entry.get("mystery") or FALLBACK
        row = {"key": key, "code": entry.get("code"), "name": entry.get("name"), "course": entry.get("course"),
               "url": entry.get("url"), "status": entry["status"], "event": entry.get("event") or "new",
               "detected_at": _ts(entry.get("detected_at")), "revealed_at": _ts(entry.get("revealed_at")),
               "emoji": mystery["emoji"], "color": mystery["color"], "dot": mystery["dot"],
               "teaser": mystery.get("teaser")}
        # La valeur n'entre dans la vue qu'une fois la note découverte
        if entry["status"] == "revealed":
            row.update(revealed_fields(entry, entry.get("workbook") or {}))
        rows.append(row)
    rows.sort(key=lambda r: (r["status"] != "mystery", -(r["revealed_at"] or r["detected_at"] or 0)))
    return {
        "version": VERSION, "generated_at": now, "slug": cfg.slug, "env_name": cfg.env_root.name,
        "enabled": bool(conf.get("enabled")), "interval_minutes": int(conf.get("interval_minutes") or 15),
        "launchd_label": schedule.label(cfg, "notes"),
        "schedule_installed": schedule.installed(cfg, "notes"),
        "running": running, "pid": os.getpid() if running else None,
        "last_check": _ts(state.get("last_check")), "last_attempt": _ts(state.get("last_attempt")),
        "last_error": state.get("last_error"),
        "last_error_kind": state.get("last_error_kind") if state.get("last_error") else None,
        "tracked": len(state["items"]),
        "mysteries": sum(1 for r in rows if r["status"] == "mystery"),
        "grades": rows[:MAX_VIEW],
        "requests_dir": str(requests_dir(cfg)),
        "check_argv": [str(Path(sys.executable).resolve()), "-I", str(schedule.entry_script()), "notes", "check",
                       "--config", str(cfg.path), "--quiet"],
    }


def write_view(cfg, state, running=False):
    # L'enveloppe d'abord : un mystère visible dans la vue doit toujours pouvoir s'ouvrir tout de suite
    util.write_json_atomic(sealed_path(cfg), build_sealed(cfg, state))
    util.write_json_atomic(view_path(cfg), build_view(cfg, state, running))


# ---------------------------------------------------------------- passage complet

def _reveal_and_report(cfg, state, log, runner):
    revealed = take_requests(cfg, state, log)
    if revealed:
        save_state(cfg, state)
        write_view(cfg, state, running=True)  # la note apparaît tout de suite, avant même le classeur
    sync_workbook(cfg, state, log, runner)
    return revealed


def run(cfg, client_factory, log, engine=None, notify=True, describe=None, runner=run_writer):
    """Un passage : révélations demandées et report dans le classeur, puis relevé des notes sur Moodle.

    Les révélations passent en premier et ne dépendent pas du réseau : un clic sur « Découvrir » fonctionne même
    quand Moodle est injoignable.
    """
    with sync.Lock(lock_path(cfg), stale_after=900):
        state = load_state(cfg)
        revealed = _reveal_and_report(cfg, state, log, runner)
        state["last_attempt"] = util.iso_now()
        write_view(cfg, state, running=True)
        try:
            found, checked, errors = fetch(cfg, client_factory(), state)
        except Exception as exc:
            message, kind = describe(exc) if describe else (str(exc), None)
            state.update(last_error=message, last_error_kind=kind)
            state["history"].append({"at": state["last_attempt"], "ok": False, "error": message})
            save_state(cfg, state)
            write_view(cfg, state)
            raise
        fresh = merge(state, found, checked, util.iso_now())
        retry = [k for k, e in state["items"].items() if k not in fresh and e.get("status") == "mystery"
                 and not (e.get("mystery") or {}).get("by")
                 and (e.get("mystery") or {}).get("attempts", 0) < MYSTERY_ATTEMPTS]
        model_error = make_mysteries(state, fresh + retry, engine, log) if fresh or (retry and engine) else None
        state.update(last_check=util.iso_now(), last_error="; ".join(errors) or None,
                     last_error_kind="course" if errors else None)
        state["history"].append({"at": state["last_check"], "ok": True, "new": len(fresh), "errors": len(errors)})
        save_state(cfg, state)
        write_view(cfg, state, running=True)
        if notify and fresh:
            notify_mysteries([state["items"][k] for k in fresh])
        # Clics arrivés pendant le relevé : launchd ignore une relance pendant que la tâche tourne
        revealed += _reveal_and_report(cfg, state, log, runner)
        save_state(cfg, state)
        write_view(cfg, state)
    return {"new": fresh, "revealed": revealed, "errors": errors, "model_error": model_error,
            "checked": len(checked), "tracked": len(found)}


# ---------------------------------------------------------------- installation d'openpyxl

def setup(log=print):
    """Python dédié muni d'openpyxl pour écrire le classeur (le pont lui-même reste en bibliothèque standard)."""
    python = VENV_PYTHON
    if not python.exists():
        log(f"Création de l'environnement Python : {VENV}")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", OPENPYXL],
                   check=True)
    version = subprocess.run([str(python), "-I", "-c", "import openpyxl; print(openpyxl.__version__)"],
                             capture_output=True, text=True, check=True).stdout.strip()
    log(f"openpyxl {version} prêt : {python}")
    return python
