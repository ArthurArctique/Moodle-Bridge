"""Démo animée du README : compile l'application en mode démo, la fait jouer, assemble les GIF.

Lancer depuis n'importe où, sur un Mac avec les outils en ligne de commande d'Apple et Pillow :

    python3 menubar/demo/make_gif.py          # docs/img/demo-clair.gif et docs/img/demo-sombre.gif

Les données affichées sont fictives (Demo.swift) : aucun cours ni aucune note réels. Rien n'apparaît à l'écran
pendant le tournage, et l'état du pont n'est jamais touché.
"""

import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from mb import menubar  # noqa: E402

OUT = REPO / "docs" / "img"
THEMES = ("clair", "sombre")


def build(tmp):
    binary = Path(tmp) / "MoodleBridgeDemo"
    arch = platform.machine() or "arm64"
    command = [menubar._swiftc(), "-parse-as-library", "-swift-version", "5", "-D", "DEMO",
               "-target", f"{arch}-apple-macos{menubar.MIN_MACOS}",
               *map(str, menubar.SOURCES), str(HERE / "Demo.swift"), "-o", str(binary)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(result.stderr.strip()[-3000:] or "swiftc a échoué")
    return binary


def assemble(frames_dir, target):
    from PIL import Image

    manifest = json.loads((frames_dir / "frames.json").read_text())["frames"]
    images = [Image.open(frames_dir / frame["file"]).convert("RGB") for frame in manifest]
    # Une seule palette pour tout le GIF, sans tramage : une zone qui ne bouge pas garde exactement les mêmes
    # pixels, et Pillow n'enregistre alors que la partie qui change d'une image à l'autre
    sample = Image.new("RGB", (images[0].width, images[0].height * 4))
    for i, image in enumerate(images[:: max(len(images) // 4, 1)][:4]):
        sample.paste(image, (0, i * image.height))
    palette = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    durations = [round(frame["ms"] / 10) * 10 for frame in manifest]
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=False)


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow manque : python3 -m pip install pillow")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="moodle-bridge-demo-") as tmp:
        binary = build(tmp)
        for theme in THEMES:
            frames = Path(tmp) / theme
            subprocess.run([str(binary), str(frames), theme], check=True)
            target = OUT / f"demo-{theme}.gif"
            assemble(frames, target)
            print(f"{target.relative_to(REPO)} : {target.stat().st_size / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
