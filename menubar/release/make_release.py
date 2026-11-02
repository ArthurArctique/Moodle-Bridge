"""Release macOS : Moodle Bridge.app universelle (Apple Silicon et Intel), pont embarqué, icône, zippée.

Produit dist/Moodle-Bridge-macOS.zip, au nom fixe pour que le lien du README mène toujours à la dernière version.
L'application est signée localement (ad hoc), sans compte développeur Apple : au premier lancement, macOS
demande de confirmer l'ouverture (« Ouvrir quand même »).

Usage : python3 -I menubar/release/make_release.py [--expect-tag v1.2.0]
Lancé par GitHub Actions (.github/workflows/release.yml) à chaque tag v*.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from mb import __version__, menubar  # noqa: E402

ARCHIVE = "Moodle-Bridge-macOS.zip"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect-tag", help="tag de la release (vX.Y.Z) : doit correspondre à la version du pont")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="dossier de l'archive (par défaut dist/)")
    args = parser.parse_args()
    if args.expect_tag and args.expect_tag.removeprefix("v") != __version__:
        sys.exit(f"le tag {args.expect_tag} ne correspond pas à la version du pont ({__version__}, "
                 "scripts/mb/__init__.py) : mets-les d'accord avant de publier")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with tempfile.TemporaryDirectory(prefix="moodle-bridge-release-") as tmp:
        work = Path(tmp)
        try:
            icon = menubar.make_icon(work)
            app = menubar.build(work / "app", archs=("arm64", "x86_64"), with_bridge=True, icon=icon)
        except menubar.BuildError as exc:
            sys.exit(f"fabrication impossible : {exc}")
        check = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        if check.returncode != 0:
            sys.exit(f"signature invalide : {check.stderr.strip()}")
        if archive.exists():
            archive.unlink()
        # ditto garde les attributs et la signature de l'application, comme le Finder
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
        version = (app / "Contents" / "Resources" / "bridge" / "bridge-version").read_text("utf-8").strip()
    print(f"{archive} ({archive.stat().st_size / 1e6:.1f} Mo) — Moodle Bridge {__version__}, pont {version}")
    return 0


if __name__ == "__main__":
    shutil.which("ditto") or sys.exit("ditto introuvable : la release se fabrique sous macOS")
    sys.exit(main())
