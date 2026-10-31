"""Choix de la destination locale d'un fichier Moodle : voisins, miroir, puis modèle de langage.

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
    "Si une information te manque pour un fichier, donne-lui une confiance basse."
)


class _NoEngine(Exception):
    pass


ClassifierError = llm.EngineError if llm else _NoEngine


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
    raise ClassifierError("réponse du modèle illisible")


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
        for entry in parse_answer(classifier.ask(prompt)):
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
