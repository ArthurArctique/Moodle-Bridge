"""Application de barre des menus (SwiftUI), compilée sur place avec les outils en ligne de commande d'Apple.

Elle ne parle jamais à Moodle : elle lit l'état que le pont écrit à chaque appel (voir snapshot.py) et
relance la tâche planifiée pour une synchronisation immédiate. Une seule application sert tous les
environnements installés sur le Mac. Sa fenêtre d'installation lance le pont : celui du dépôt pour une
application compilée sur place, celui qu'elle embarque pour l'application de la release (make_release.py).
"""

import hashlib
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
ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted((ROOT / "menubar").glob("*.swift"))
ICON_SOURCE = ROOT / "menubar" / "release" / "make_icon.swift"
# Réponse de la connexion par le navigateur, adressée à l'appli mobile : l'application la reçoit à sa place
URL_SCHEME = "moodlemobile"
BRIDGE_FILES = ("scripts", "skill", "LICENSE")


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


def info_plist(repo=None, icon=False):
    data = {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXECUTABLE, "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
        "CFBundleDevelopmentRegion": "fr", "LSMinimumSystemVersion": MIN_MACOS,
        # Pas d'icône dans le Dock : l'application vit dans la barre des menus
        "LSUIElement": True, "NSHighResolutionCapable": True,
        "CFBundleURLTypes": [{"CFBundleURLName": f"{BUNDLE_ID}.login", "CFBundleURLSchemes": [URL_SCHEME]}],
    }
    if icon:
        data["CFBundleIconFile"] = "AppIcon"
    if repo:
        data["MoodleBridgeRepo"] = str(repo)  # pont lancé par la fenêtre d'installation
    return data


def _compile(arch, output):
    command = [_swiftc(), "-O", "-parse-as-library", "-swift-version", "5",
               "-target", f"{arch}-apple-macos{MIN_MACOS}", *map(str, SOURCES), "-o", str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise BuildError(result.stderr.strip()[-3000:] or "swiftc a échoué")


def make_icon(work_dir):
    """AppIcon.icns, dessinée par make_icon.swift puis déclinée en toutes les tailles (sips, iconutil)."""
    work = Path(work_dir)
    tool, master, iconset = work / "make_icon", work / "icon-1024.png", work / "AppIcon.iconset"
    steps = [[_swiftc(), "-O", "-target", f"{platform.machine() or 'arm64'}-apple-macos{MIN_MACOS}",
              str(ICON_SOURCE), "-o", str(tool)], [str(tool), str(master)]]
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            pixels = str(size * scale)
            steps.append(["sips", "-z", pixels, pixels, str(master), "--out", str(iconset / name)])
    icon = work / "AppIcon.icns"
    steps.append(["iconutil", "-c", "icns", str(iconset), "-o", str(icon)])
    for command in steps:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise BuildError(f"icône : {Path(command[0]).name} a échoué ({result.stderr.strip()[-500:]})")
    return icon


def bundle_bridge(dest):
    """Copie le pont (scripts, modèle du skill, licence) et note sa version : l'application le recopie hors
    d'elle-même quand elle change, pour que la tâche planifiée ne dépende pas de l'endroit où elle se trouve."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for name in BRIDGE_FILES:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, dest / name, ignore=ignore)
        else:
            shutil.copy2(source, dest / name)
    digest = hashlib.sha256()
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(dest)).encode() + b"\0" + path.read_bytes())
    (dest / "bridge-version").write_text(f"{__version__}+{digest.hexdigest()[:12]}\n", encoding="utf-8")


def build(out_dir, archs=None, with_bridge=False, icon=None, repo=None):
    """`archs` : plusieurs architectures donnent une application universelle ; `with_bridge` embarque le pont,
    `icon` (.icns) l'icône ; `repo` : dépôt dont la fenêtre d'installation lance le pont."""
    if not SOURCES:
        raise BuildError(f"sources de l'application introuvables dans {ROOT / 'menubar'}")
    app = Path(out_dir) / f"{APP_NAME}.app"
    macos, resources = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    archs = list(archs or [platform.machine() or "arm64"])
    if len(archs) == 1:
        _compile(archs[0], macos / EXECUTABLE)
    else:
        slices = [Path(out_dir) / f"{EXECUTABLE}-{arch}" for arch in archs]
        for arch, path in zip(archs, slices):
            _compile(arch, path)
        result = subprocess.run(["lipo", "-create", *map(str, slices), "-output", str(macos / EXECUTABLE)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise BuildError(result.stderr.strip() or "lipo a échoué")
        for path in slices:
            path.unlink()
    if with_bridge or icon:
        resources.mkdir(parents=True)
    if with_bridge:
        (resources / "bridge").mkdir()
        bundle_bridge(resources / "bridge")
    if icon:
        shutil.copy2(icon, resources / "AppIcon.icns")
    with open(app / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump(info_plist(repo=repo, icon=bool(icon)), handle)
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
        try:
            icon = make_icon(Path(tmp))
        except BuildError:
            icon = None  # l'icône n'est visible que pendant la fenêtre d'installation : s'en passer
        built = build(Path(tmp) / "app", repo=ROOT, icon=icon)
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
