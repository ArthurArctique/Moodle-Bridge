"""« L'essentiel » : ce qui compte en ce moment, tiré de l'état sans modèle de langage.

Le même résumé ouvre l'accueil du coffre et le premier onglet de l'application :

- les problèmes du pont : dernier passage en échec, classement automatique en panne ;
- les échéances des 7 prochains jours ;
- les examens et évaluations : échéances jusqu'à 30 jours, annonces récentes qui en parlent ;
- les changements annoncés : report, annulation, salle, distanciel, campus bloqué… ;
- ce qui attend une action : fichiers à classer, supports à importer dans Zotero, notes à découvrir, cours non
  configurés.

Les annonces sont triées sur leur seul titre, par mots-clés, sans tenir compte des accents ni des majuscules. Un
titre qui annonce un changement (« Report des CC ») est rangé dans les changements, pas aussi dans les examens.
"""

import re
import time
import unicodedata

from .index import recent_announcements, upcoming

WEEK_DAYS = 7
EXAM_DAYS = 30
EXAM_ANNOUNCEMENT_DAYS = 14
MAX_ROWS = 6

# Mots cherchés dans un texte en minuscules et sans accents (« Reporté » → « reporte »)
CHANGE = re.compile(
    r"\b(?:annul|report|decal|deplac|changement|change\b|modifi|remplac|salles?\b|distanciel|en visio|"
    r"bloqu|blocage|greve|fermee?s?\b|fermeture|urgent|horaires?\b|nouvel(?:le)? (?:date|horaire|salle)|"
    r"ne pas venir|pas de (?:cours|td|tp|cm|visio|seance))")
EXAM = re.compile(
    r"\b(?:examens?|exams?|partiels?|controles?|ccs?\d*|ds\d*|qcm|evaluations?|evalue|soutenances?|oral|oraux|"
    r"rattrapages?|convocations?|restitutions?|(?:tp|td|devoir|projet|exercice|quiz)s? notes?)\b")
# Ni une annonce de notes (« Notes TP noté BOB », l'onglet Notes s'en charge), ni l'évaluation des enseignements
NOT_EXAM = re.compile(r"^\W*notes?\b|evaluation des enseignements")


def plain(text):
    """Texte en minuscules, sans accents ni apostrophes typographiques."""
    text = unicodedata.normalize("NFKD", str(text or "").lower().replace("’", "'"))
    return "".join(c for c in text if not unicodedata.combining(c))


def is_change(title):
    return bool(CHANGE.search(plain(title)))


def is_exam(title):
    title = plain(title)
    return bool(EXAM.search(title)) and not NOT_EXAM.search(title)


def build(cfg, state, now=None, mysteries=0):
    """Résumé de l'état ; `mysteries` : nombre de notes à découvrir (le relevé des notes a son propre état).

    Échéances et annonces gardent leurs champs d'origine (`courseid`, `section`, `cmid`…), plus `at` et
    `source` (« deadline » ou « announcement ») : l'accueil et l'application les mettent en forme chacun à sa façon.
    """
    now = int(now or time.time())
    remote = state.get("remote", {})
    items = state.get("items", {})
    days = int(cfg.data["index"].get("news_days") or 7)

    problems = []
    if state.get("last_error"):
        problems.append({"kind": "sync", "message": state["last_error"], "error_kind": state.get("last_error_kind")})
    if (state.get("classify_error") or {}).get("message"):
        problems.append({"kind": "classify", "message": state["classify_error"]["message"],
                         "at": state["classify_error"].get("at")})

    deadlines = upcoming(cfg, remote, now, limit=None)
    week_end, exam_end = now + WEEK_DAYS * 86400, now + EXAM_DAYS * 86400
    week = [dict(row, source="deadline", exam=is_exam(row["name"])) for row in deadlines if row["at"] <= week_end]
    # Les évaluations de la semaine sont déjà dans `week` (marquées `exam`) : seules les suivantes s'ajoutent
    exams = [dict(row, source="deadline") for row in deadlines
             if week_end < row["at"] <= exam_end and is_exam(row["name"])]

    changes = []
    for ann in recent_announcements(remote, max(days, EXAM_ANNOUNCEMENT_DAYS), now):
        if not cfg.course(ann["courseid"]):
            continue
        row = dict(ann, at=ann["time"], source="announcement")
        if is_change(ann.get("name")):
            if ann["time"] >= now - days * 86400:
                changes.append(row)
        elif is_exam(ann.get("name")):
            exams.append(row)

    counts = {}
    for entry in items.values():
        counts[entry.get("status")] = counts.get(entry.get("status"), 0) + 1
    unconfigured = sorted(map(str, (remote.get("unconfigured") or {}).values()))
    actions = {"inbox": counts.get("inbox", 0), "zotero_pending": counts.get("zotero_pending", 0),
               "mysteries": mysteries, "unconfigured": unconfigured}
    return {
        "problems": problems,
        "week": week,
        # Annonces d'abord (la plus récente en tête), puis échéances par date
        "exams": sorted(exams, key=lambda r: (r["source"] != "announcement",
                                              -r["at"] if r["source"] == "announcement" else r["at"])),
        "changes": changes,
        "actions": actions,
    }


def is_empty(data):
    return not (data["problems"] or data["week"] or data["exams"] or data["changes"] or any(data["actions"].values()))
