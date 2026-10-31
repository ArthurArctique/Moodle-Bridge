"""Ce qui dépend du système : dossiers de cache et de données, notifications, presse-papiers, processus, Obsidian.

Le pont tourne sous macOS, Windows et Linux avec la seule bibliothèque standard de Python. Chaque fonction
d'ici échoue en silence quand l'outil du système manque (une notification perdue n'arrête jamais un passage).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"
IS_LINUX = not IS_MAC and not IS_WINDOWS
APP_DIR = "moodle-bridge"
# Sous Windows, une commande lancée en arrière-plan ne doit pas ouvrir de fenêtre de console
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def name():
    return "macOS" if IS_MAC else "Windows" if IS_WINDOWS else "Linux"


def _xdg(variable, fallback):
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / fallback


def _local_appdata():
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


def cache_root():
    """Cache du pont (téléchargements en cours, état pour l'application), hors de l'espace de travail."""
    if IS_MAC:
        return Path.home() / "Library" / "Caches" / APP_DIR
    if IS_WINDOWS:
        return _local_appdata() / APP_DIR / "Cache"
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_DIR


def data_root():
    """Données durables propres au Mac ou au PC (environnement Python d'openpyxl…)."""
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_DIR
    if IS_WINDOWS:
        return _local_appdata() / APP_DIR
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_DIR


def log_root():
    """Sorties des tâches planifiées."""
    if IS_MAC:
        return Path.home() / "Library" / "Logs" / APP_DIR
    return data_root() / "logs"


def pid_alive(pid):
    """Vrai si le processus existe encore (un verrou dont le processus a disparu est abandonné)."""
    pid = int(pid)
    if pid <= 0:
        return False
    if IS_WINDOWS:
        # os.kill(pid, 0) enverrait Ctrl+C sous Windows : interroger le processus sans y toucher
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_quiet(args, timeout=15, **kwargs):
    """Lance une commande du système sans fenêtre ni sortie ; None si elle est introuvable ou échoue."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None


def _applescript(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


# Notification Windows (toast) par PowerShell, sans module à installer ; l'identifiant d'application
# est celui de PowerShell, toujours enregistré auprès du centre de notifications
WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:MOODLE_BRIDGE_TOAST)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def notify(title, message, subtitle=None):
    """Notification du système ; silencieuse si l'outil manque."""
    if IS_MAC:
        script = f"display notification {_applescript(message)} with title {_applescript(title)}"
        if subtitle:
            script += f" subtitle {_applescript(subtitle)}"
        run_quiet(["osascript", "-e", script], timeout=10)
    elif IS_WINDOWS:
        lines = "".join(f"<text>{_xml(t)}</text>" for t in (title, subtitle, message) if t)
        toast = f"<toast><visual><binding template='ToastGeneric'>{lines}</binding></visual></toast>"
        run_quiet(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                   WINDOWS_TOAST], timeout=20, env={**os.environ, "MOODLE_BRIDGE_TOAST": toast})
    elif shutil.which("notify-send"):
        run_quiet(["notify-send", title, f"{subtitle}\n{message}" if subtitle else message], timeout=10)


def read_clipboard():
    """Texte du presse-papiers, ou None si aucun outil ne permet de le lire."""
    if IS_MAC:
        commands = [["pbpaste"]]
    elif IS_WINDOWS:
        commands = [["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]]
    else:
        commands = [["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"], ["xsel", "-b", "-o"]]
    for command in commands:
        if shutil.which(command[0]):
            result = run_quiet(command)
            if result and result.returncode == 0:
                return result.stdout
    return None


def clear_clipboard():
    if IS_MAC:
        run_quiet(["pbcopy"], input="")
    elif IS_WINDOWS:
        run_quiet(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $null"])
    elif shutil.which("wl-copy"):
        run_quiet(["wl-copy", "--clear"])
    elif shutil.which("xclip"):
        run_quiet(["xclip", "-selection", "clipboard"], input="")


def background_python():
    """Interpréteur pour les tâches planifiées : pythonw sous Windows, pour ne pas ouvrir de console."""
    python = Path(sys.executable).resolve()
    if IS_WINDOWS:
        windowless = python.with_name("pythonw.exe")
        if windowless.exists():
            return windowless
    return python


def find_command(command):
    """Chemin complet d'une commande (gère les extensions .exe/.cmd de Windows), ou None."""
    if not command:
        return None
    path = Path(command).expanduser()
    if path.is_absolute():
        return str(path) if path.exists() else None
    return shutil.which(str(command))


# ---------------------------------------------------------------- Obsidian (facultatif)

def _obsidian_config():
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "obsidian" / "obsidian.json"
    return _xdg("XDG_CONFIG_HOME", ".config") / "obsidian" / "obsidian.json"


def obsidian_installed():
    if _obsidian_config().exists():
        return True
    if IS_MAC:
        return any(p.exists() for p in (Path("/Applications/Obsidian.app"), Path.home() / "Applications" / "Obsidian.app"))
    if IS_WINDOWS:
        return (_local_appdata() / "Programs" / "Obsidian" / "Obsidian.exe").exists()
    return bool(shutil.which("obsidian"))


def obsidian_vaults():
    """Coffres connus d'Obsidian (chemins existants), le plus récemment ouvert d'abord."""
    try:
        data = json.loads(_obsidian_config().read_text("utf-8"))
    except (OSError, ValueError):
        return []
    vaults = [v for v in (data.get("vaults") or {}).values() if isinstance(v, dict) and v.get("path")]
    vaults.sort(key=lambda v: -(v.get("ts") or 0))
    return [Path(v["path"]) for v in vaults if Path(v["path"]).is_dir()]
