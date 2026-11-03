"""Choix de la destination locale d'un fichier Moodle : voisins, séance évidente, miroir, puis modèle de langage.

Toute destination proposée par un modèle est validée ici : elle reste sous la racine du cours
et doit désigner un dossier existant ou un dossier conforme aux motifs autorisés.
"""

import json
import os
import re
from pathlib import Path

from . import util

try:
    from . import llm
except ImportError:  # édition sans modèle de langage : rangement par règles seulement
    llm = None

PLACED = {"placed", "present", "updated"}
BATCH_SIZE = 40
MAX_EXAMPLES = 80
MAX_TREE_LINES = 400
# Remplace la consigne d'assistant de code de la ligne de commande : le modèle n'est ici qu'un classeur
SYSTEM_PROMPT = (
    "Tu es un module de classement de fichiers appelé par un script, sans humain dans la boucle. "
    "Tu ne converses pas et tu ne poses jamais de question : tu lis la demande et tu réponds uniquement "
    "par l'objet JSON demandé, sans texte avant ni après et sans balises de code. "
    "Tu n'as accès à aucun fichier, aucune commande ni aucun outil, et tu n'en as pas besoin : tout ce qu'il "
    "te faut est dans la demande. "
    "Si une information te manque pour un fichier, donne-lui une confiance basse."
)
# Ajouté à une seconde demande quand la première n'a rien donné d'exploitable
RETRY_NOTE = ("\n\nRappel : ta réponse précédente était vide ou illisible. N'essaie d'ouvrir aucun fichier "
              "ni de lancer aucune commande : tout est ci-dessus. Réponds directement par l'objet JSON demandé.")


class _NoEngine(Exception):
    pass


ClassifierError = llm.EngineError if llm else _NoEngine
NoAnswer = llm.NoAnswer if llm else _NoEngine


def normalize_dest(dest):
    """Chemin relatif propre (« TD/TD03 ») ou None s'il sort de la racine."""
    if not isinstance(dest, str):
        return None
    dest = util.nfc(dest).strip().replace("\\", "/")
    if dest.startswith(("/", "~")):
        return None
    while dest.startswith("./"):
        dest = dest[2:]
    dest = dest.strip("/")
    if not dest:
        return None
    parts = [p for p in dest.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        return None
    return "/".join(parts)


def validate_dest(dest, root, patterns, inbox, special=()):
    """Destination acceptée (relative au cours, ou jeton spécial comme « @zotero ») ou None."""
    if isinstance(dest, str) and dest.strip() in special:
        return dest.strip()
    dest = normalize_dest(dest)
    if dest is None or dest.split("/")[0] == inbox or dest.startswith("@"):
        return None
    root = Path(root)
    target = (root / dest).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if target.is_dir():
        return dest
    if any(re.fullmatch(pattern, dest) for pattern in patterns):
        return dest
    return None


def dest_dir_for(root, path, filepath):
    """Dossier de rangement (relatif à la racine du cours) d'un fichier local, sans le sous-chemin Moodle."""
    try:
        rel = Path(path).parent.relative_to(root).as_posix()
    except ValueError:
        return None
    sub = (filepath or "/").strip("/")
    if sub and (rel == sub or rel.endswith("/" + sub)):
        rel = rel[: -len(sub)].rstrip("/")
    return rel or "."


def mirror_dest(item):
    return util.safe_name(item.get("section") or "Sans section", 80)


def sibling_dest(item, evidence, inbox, min_agreeing=2):
    """Dossier commun aux fichiers déjà rangés de la même activité Moodle (un dossier de jeux de données…).

    Il faut au moins `min_agreeing` fichiers concordants et aucun désaccord : un seul exemple ne suffit pas,
    car une activité ou une section peut regrouper plusieurs séances. La section seule n'est jamais
    une règle ; elle sert d'exemple au modèle.
    """
    dirs = [e["dest_dir"] for e in evidence
            if e["courseid"] == item["courseid"] and e["cmid"] == item["cmid"] and e.get("dest_dir")
            and e["dest_dir"].split("/")[0] != inbox]
    if len(dirs) >= min_agreeing and len(set(dirs)) == 1:
        return dirs[0], "voisins"
    return None


# « TD2 », « TP 03 », « td_4 » ; un intervalle (« TD5-6 », « TP 1 et 2 ») compte pour deux séances
SESSION = re.compile(r"(?<![a-z])(cm|td|tp)[\s_.-]?0*(\d{1,3})(?!\d)"
                     r"(?:\s*(?:[-–/&,+]|et|à|and|to)\s*0*(\d{1,3})(?!\d))?", re.IGNORECASE)
# Type de séance nommé sans numéro : « TP du 21 septembre », « Practicals 5-6 »
KIND_WORDS = {"CM": r"cms?|cours magistra(?:l|ux)|lectures?", "TD": r"tds?|travaux dirigés|tutorials?",
              "TP": r"tps?|travaux pratiques|practicals?"}
KINDS = {kind: re.compile(rf"(?<![a-z])(?:{words})(?![a-z])", re.IGNORECASE) for kind, words in KIND_WORDS.items()}


def session_marks(text):
    """Séances nommées dans un texte : {("TD", 2)}."""
    marks = set()
    for kind, first, last in SESSION.findall(util.nfc(text or "")):
        marks.add((kind.upper(), int(first)))
        if last:
            marks.add((kind.upper(), int(last)))
    return marks


def session_of(item):
    """Le TD ou TP que nomment l'activité ou le fichier, si rien ne le contredit ; sinon None.

    La section peut confirmer ou contredire, jamais suffire : elle regroupe souvent plusieurs séances. Un seul
    type et un seul numéro dans la section, l'activité et le nom du fichier ; les CM restent au modèle (support
    pour Zotero, ou fichier d'accompagnement).
    """
    names = [item.get("module"), Path(item.get("filename") or "").stem]
    texts = names + [item.get("section")]
    marks = set().union(*map(session_marks, texts))
    kinds = {kind for text in texts for kind, words in KINDS.items() if words.search(util.nfc(text or ""))}
    if len(marks) != 1 or len(kinds | {kind for kind, _ in marks}) != 1:
        return None
    session = next(iter(marks))
    if session[0] == "CM" or not any(map(session_marks, names)):
        return None
    return session


def session_dest(item, root, patterns, inbox, known=()):
    """Dossier d'une séance évidente (« GNF_TD2_2026-2027 » → TD/TD02), ou None.

    `known` : fichiers déjà rangés du même cours. Si l'un d'eux nomme une séance de ce type mais n'est pas dans
    le dossier de ce numéro, la numérotation locale diffère de celle de Moodle : la règle s'efface et le modèle
    tranche. Le dossier est celui qui existe déjà pour cette séance, ou un nouveau dossier formé comme ceux des
    autres séances du même type, toujours validé par `new_dir_patterns`.
    """
    session = session_of(item)
    if not session:
        return None
    kind, number = session
    for other in known:
        past = session_of(other)
        if past and past[0] == kind and session_marks(Path(other["dest_dir"]).name) != {past}:
            return None
    existing, models = [], []
    for rel in _course_dirs(root, inbox):
        marks = session_marks(Path(rel).name)
        if marks == {session}:
            existing.append(rel)
        elif len(marks) == 1 and next(iter(marks))[0] == kind:
            models.append(rel)
    if existing:
        return (existing[0], "séance évidente") if len(existing) == 1 else None
    candidates = [_renumber(rel, number) for rel in models] + [f"{kind}/{kind}{number:02d}", f"{kind}{number:02d}"]
    for candidate in candidates:
        if candidate and validate_dest(candidate, root, patterns, inbox):
            return candidate, "séance évidente"
    return None


def _course_dirs(root, inbox, depth=2):
    """Dossiers du cours sur `depth` niveaux (« TD », « TD/TD01 »…), hors boîte d'attente et dossiers cachés."""
    root = Path(root)
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        keep = sorted(d for d in dirnames if not d.startswith(".") and (rel.parts or d != inbox))
        dirnames[:] = keep if len(rel.parts) < depth else []
        if rel.parts:
            found.append(rel.as_posix())
    return found


def _renumber(rel, number):
    """« TD/TD01 » → « TD/TD07 » : même forme, même nombre de chiffres ; seul le dernier élément change."""
    head, _, name = rel.rpartition("/")
    match = re.search(r"(?<![a-z])(?:cm|td|tp)[\s_.-]?(\d+)", name, re.IGNORECASE)
    if not match:
        return None
    new = name[:match.start(1)] + f"{number:0{len(match.group(1))}d}" + name[match.end(1):]
    return f"{head}/{new}" if head else new


def course_tree(root, inbox, limit=MAX_TREE_LINES):
    root = Path(root)
    if not root.exists():
        return ["(dossier pas encore créé)"]
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != inbox)
        rel = Path(dirpath).relative_to(root).as_posix()
        depth = 0 if rel == "." else rel.count("/") + 1
        if rel != ".":
            lines.append("  " * (depth - 1) + Path(dirpath).name + "/")
        for name in sorted(f for f in filenames if not f.startswith(".")):
            lines.append("  " * depth + name)
        if len(lines) >= limit:
            lines.append("… (arborescence tronquée)")
            break
    return lines or ["(dossier vide)"]


def context_excerpt(env_root, files, code, max_full=6000):
    """Contenu des fichiers de contexte ; pour un gros fichier, seulement les lignes qui citent le cours."""
    chunks = []
    word = re.compile(rf"\b{re.escape(code)}\b") if code else None
    for rel in files:
        path = Path(env_root) / rel
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        if len(text) > max_full and word:
            lines = text.splitlines()
            kept = lines[:6] + [line for line in lines[6:] if word.search(line)]
            text = "\n".join(kept)
        chunks.append(f"### {rel}\n{text.strip()}")
    return "\n\n".join(chunks) or "(aucun)"


def build_prompt(conf, course_name, rules, context, tree, examples, pending, min_confidence, inbox,
                 zotero_token=None):
    example_lines = [f"- {e['section']} | {e['module']} | {e['filename']} → {e['dest_dir']}"
                     for e in examples[:MAX_EXAMPLES]] or ["(aucun pour l'instant)"]
    zotero_rule = ""
    if zotero_token:
        zotero_rule = (f'\n- Les supports de cours magistraux (diaporamas ou polycopiés de CM, chapitres et résumés '
                       f'de cours, livres, articles) ne se rangent PAS dans l\'arborescence : ils vont dans la '
                       f'bibliothèque Zotero. Pour eux, réponds exactement dest = "{zotero_token}".'
                       f'\n- Tout ce qui relève d\'un TD ou d\'un TP reste TOUJOURS dans l\'arborescence, quelle que '
                       f'soit sa forme : énoncé, diapositives ou présentation de TD/TP, corrigé, données, scripts. '
                       f'Un diaporama de TD n\'est pas un support de cours.')
    pending_lines = [
        f"- id={p['id']} | section : {p['section']} | activité : {p['module']} ({p['modname']}) | "
        f"fichier : {p['filename']} | {util.fmt_date(p['timemodified'])}"
        for p in pending
    ]
    return f"""Tu ranges des fichiers téléchargés depuis Moodle dans l'arborescence locale d'un cours.

Réponds UNIQUEMENT par un objet JSON, sans texte autour :
{{"placements": [{{"id": "<id fourni>", "dest": "<dossier relatif>", "confidence": 0.0, "reason": "<courte justification>"}}]}}

Consignes :
- "dest" est un dossier RELATIF à la racine du cours (jamais de chemin absolu, jamais « .. »).
- Si le fichier appartient à une séance déjà rangée localement, réutilise exactement ce dossier, même si
  l'intitulé Moodle dit autre chose (la numérotation locale fait foi).
- Sinon, propose un dossier qui respecte les règles de rangement ci-dessous.
- "confidence" entre 0 et 1. En dessous de {min_confidence}, le fichier ira dans « {inbox} » pour qu'un
  humain le range : baisse la confiance dès que le type de séance ou le numéro est incertain.
- Une entrée par fichier, avec l'id fourni.{zotero_rule}

## Règles de rangement
{rules.strip() or "(aucune règle fournie)"}

## Contexte de l'environnement
{context}

## Cours
Code : {conf.get("code")} — {course_name}

## Arborescence locale actuelle (racine du cours)
{chr(10).join(tree)}

## Fichiers Moodle déjà rangés (section | activité | fichier → dossier)
{chr(10).join(example_lines)}

## Fichiers à ranger
{chr(10).join(pending_lines)}
"""


def parse_answer(text):
    """Liste de placements depuis la réponse du modèle (tolère des balises de code autour)."""
    text = (text or "").strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("placements", [])
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and "id" in d]
    raise NoAnswer("réponse du modèle illisible")


def ask_placements(classifier, prompt):
    """Placements proposés ; une seconde demande, plus ferme, si la première n'a rien donné d'exploitable."""
    try:
        return parse_answer(classifier.ask(prompt))
    except NoAnswer:
        return parse_answer(classifier.ask(prompt + RETRY_NOTE))


def make_classifier(settings):
    """Modèle de classement (`classify.engine`), ou None : les fichiers ambigus vont alors dans la boîte d'attente."""
    return llm.make_engine(settings, SYSTEM_PROMPT) if llm else None


def classify_course(classifier, conf, course_name, pending, examples, settings, context, zotero_token=None):
    """Renvoie ({clé: décision acceptée}, {clé: proposition refusée}).

    `pending` : liste de dicts avec au moins key, section, module, modname, filename, timemodified.
    """
    inbox = settings["inbox"]
    min_conf = float(settings["min_confidence"])
    patterns = settings["new_dir_patterns"]
    special = (zotero_token,) if zotero_token else ()
    tree = course_tree(conf["root"], inbox)
    accepted, refused = {}, {}
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        ids = {str(n): p["key"] for n, p in enumerate(batch, start=1)}
        numbered = [{**p, "id": n} for n, p in zip(ids, batch)]
        prompt = build_prompt(conf, course_name, settings.get("rules", ""), context, tree,
                              examples, numbered, min_conf, inbox, zotero_token)
        for entry in ask_placements(classifier, prompt):
            key = ids.get(str(entry.get("id")))
            if not key:
                continue
            try:
                confidence = float(entry.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            reason = str(entry.get("reason", ""))[:200]
            raw = entry.get("dest")
            proposed = raw.strip() if isinstance(raw, str) and raw.strip() in special else normalize_dest(raw)
            filename = next(p["filename"] for p in batch if p["key"] == key)
            if proposed and proposed not in special and proposed.split("/")[-1] == util.nfc(filename):
                proposed = proposed.rsplit("/", 1)[0] if "/" in proposed else None  # le modèle a ajouté le nom du fichier
            dest = validate_dest(proposed, conf["root"], patterns, inbox, special)
            if dest and confidence >= min_conf:
                accepted[key] = {"dest_dir": dest, "confidence": confidence, "reason": reason}
            else:
                why = "destination refusée" if not dest else f"confiance {confidence:.2f}"
                refused[key] = {"confidence": confidence,
                                "reason": f"{why} — proposition : {entry.get('dest')} ({reason})"}
    return accepted, refused
