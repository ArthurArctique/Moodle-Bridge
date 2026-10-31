#!/usr/bin/env python3
"""Installation de Moodle Bridge : lance l'assistant (site, connexion, cours, dossier, IA, synchronisation).

    python3 install.py          (macOS, Linux)
    py install.py               (Windows)

Relancer le même assistant plus tard met à jour l'installation sans rien perdre.
"""

import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit(f"Python 3.9 ou plus récent est nécessaire (version actuelle : {sys.version.split()[0]}).")

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import moodle_bridge  # noqa: E402

if __name__ == "__main__":
    sys.exit(moodle_bridge.main(["init", *sys.argv[1:]]))
