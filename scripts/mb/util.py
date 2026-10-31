"""Utilitaires partagés : écriture atomique, empreintes, noms de fichiers, journal, notifications."""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from . import system

WEEKDAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def sha256_file(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_atomic(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            writer(handle)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _replace(src, dest, attempts=5):
    """os.replace, réessayé sous Windows où un lecteur (Obsidian, un éditeur) peut tenir le fichier un instant."""
    for attempt in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if not system.IS_WINDOWS or attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def write_json_atomic(path, data):
    def writer(handle):
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    _write_atomic(path, writer)


def write_text_atomic(path, text):
    _write_atomic(path, lambda handle: handle.write(text))


def safe_name(name, maxlen=120):
    """Nom de fichier ou de dossier valable sous macOS, Windows et Linux, au plus près de l'original."""
    name = nfc(name).replace("/", "-").replace("\\", "-").replace(":", " -")
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r'[<>"|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if re.fullmatch(r"(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?", name, re.IGNORECASE):
        name = f"_{name}"
    if len(name) > maxlen:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            name = stem[: maxlen - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:maxlen].rstrip(" .")
    return name or "Sans titre"


def fmt_date(ts, with_time=False, with_weekday=False):
    if not ts:
        return "—"
    moment = datetime.fromtimestamp(ts)
    text = moment.strftime("%d/%m/%Y")
    if with_weekday:
        text = f"{WEEKDAYS[moment.weekday()]} {moment.strftime('%d/%m')}"
    if with_time:
        text += moment.strftime(" %H:%M")
    return text


def fmt_size(size):
    if size is None:
        return "?"
    if size < 1000:
        return f"{size} o"
    if size < 1_000_000:
        return f"{size / 1000:.0f} Ko"
    if size < 1_000_000_000:
        return f"{size / 1_000_000:.1f} Mo".replace(".", ",")
    return f"{size / 1_000_000_000:.2f} Go".replace(".", ",")


def fmt_ago(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "moins de 2 min"
    if seconds < 5400:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h".replace(".", ",")
    return f"{seconds // 86400} jours"


def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(text):
    return datetime.fromisoformat(text).timestamp() if text else None


def notify(title, message, subtitle=None):
    """Notification du système (macOS, Windows, Linux) ; silencieuse si elle est impossible."""
    system.notify(title, message, subtitle)


class Logger:
    """Journal horodaté dans un fichier (rotation à 1 Mo), recopié à l'écran sauf en mode silencieux."""

    def __init__(self, path=None, quiet=False, max_bytes=1_000_000):
        self.path = Path(path) if path else None
        self.quiet = quiet
        self.max_bytes = max_bytes
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, message, screen=True):
        if screen and not self.quiet:
            print(message)
        if not self.path:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self.path, "a", encoding="utf-8") as handle:
                for line in str(message).splitlines() or [""]:
                    handle.write(f"{stamp} {line}\n")
        except OSError as exc:
            print(f"(journal indisponible : {exc})", file=sys.stderr)
