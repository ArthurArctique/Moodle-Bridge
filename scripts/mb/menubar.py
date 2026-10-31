"""Application de barre des menus (SwiftUI), compilée sur place avec les outils en ligne de commande d'Apple.

Elle ne parle jamais à Moodle : elle lit l'état que le pont écrit à chaque appel (voir snapshot.py) et
relance la tâche planifiée pour une synchronisation immédiate. Une seule application sert tous les
environnements installés sur le Mac.
"""

import platform
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import __version__

APP_NAME = "Moodle Bridge"
EXECUTABLE = "MoodleBridge"
BUNDLE_ID = "local.moodle-bridge.menubar"
MIN_MACOS = "13.0"  # MenuBarExtra et l'ouverture à la connexion (SMAppService)
DEFAULT_DEST = Path.home() / "Applications"
SOURCE = Path(__file__).resolve().parents[2] / "menubar" / "MoodleBridgeMenu.swift"


class BuildError(Exception):
    pass


def _swiftc():
    found = shutil.which("swiftc")
    if found:
        return found
    result = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise BuildError("swiftc introuvable : l'utilisateur doit installer les outils en ligne de commande "
                     "d'Apple (`xcode-select --install`)")


def info_plist():
    return {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXECUTABLE, "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
        "CFBundleDevelopmentRegion": "fr", "LSMinimumSystemVersion": MIN_MACOS,
        # Pas d'icône dans le Dock : l'application vit dans la barre des menus
        "LSUIElement": True, "NSHighResolutionCapable": True,
    }


def build(out_dir):
    app = Path(out_dir) / f"{APP_NAME}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    arch = platform.machine() or "arm64"
    command = [_swiftc(), "-O", "-parse-as-library", "-swift-version", "5",
               "-target", f"{arch}-apple-macos{MIN_MACOS}", str(SOURCE), "-o", str(macos / EXECUTABLE)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise BuildError(result.stderr.strip()[-3000:] or "swiftc a échoué")
    with open(app / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump(info_plist(), handle)
    # Signature locale (ad hoc) : macOS l'exige pour l'ouverture à la connexion
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], capture_output=True)
    return app


def _own_app(path):
    """Vrai si `path` est bien cette application (jamais supprimer une autre application du même nom)."""
    try:
        with open(path / "Contents" / "Info.plist", "rb") as handle:
            return plistlib.load(handle).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def quit_running():
    subprocess.run(["pkill", "-x", EXECUTABLE], capture_output=True)


def install(dest=DEFAULT_DEST, launch=True):
    dest = Path(dest).expanduser()
    target = dest / f"{APP_NAME}.app"
    if target.exists() and not _own_app(target):
        raise BuildError(f"{target} existe déjà et n'est pas cette application")
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="moodle-bridge-app-") as tmp:
        built = build(tmp)
        quit_running()
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(built), str(target))
    if launch:
        subprocess.run(["open", str(target)], capture_output=True)
    return target


def uninstall(dest=DEFAULT_DEST):
    target = Path(dest).expanduser() / f"{APP_NAME}.app"
    if not target.exists() or not _own_app(target):
        return False
    quit_running()
    shutil.rmtree(target)
    return True
