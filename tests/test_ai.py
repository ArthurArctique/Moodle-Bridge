"""Tests du choix de l'IA : fausses commandes claude et codex, aucun appel réseau.

Lancer : python3 -I -m unittest discover <skill>/tests
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mb import ai, config, system  # noqa: E402

FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "auth" ]; then echo '{"loggedIn": %s, "email": "secret@example.com"}'; exit 0; fi
cat > /dev/null
echo '{"result": "{\\"ok\\": true}", "modelUsage": {"claude-haiku-test": {}}}'
"""
FAKE_CODEX = """#!/bin/sh
if [ "$1" = "login" ]; then exit 1; fi
if [ "$1" = "debug" ]; then
  echo '{"models": [{"slug": "petit", "display_name": "Petit", "visibility": "list"},
                    {"slug": "cache", "display_name": "Caché", "visibility": "hide"}]}'
fi
"""


def fake(folder, name, body):
    path = folder / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@unittest.skipIf(system.IS_WINDOWS, "fausses commandes en script shell")
class AiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mb-ai-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.claude = fake(self.bin, "claude", FAKE_CLAUDE % "true")
        self.codex = fake(self.bin, "codex", FAKE_CODEX)
        self.path = self.tmp / ".moodle-bridge" / "config.json"
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({
            "site": "https://moodle.test", "courses": {"1": {"dest": "A", "code": "A"}},
            "classify": {"engine": "codex-cli", "command": str(self.codex), "args": ["-c", "x=1"], "rules": "mes règles"},
            "summaries": {"model": "gros-modèle"}, "notes": {"enabled": True, "mystery": {"engine": "codex-cli"}}}))
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.old_path}"

    def tearDown(self):
        os.environ["PATH"] = self.old_path

    def test_status_lists_engines_and_models(self):
        info = ai.status(config.load(self.path))
        claude, codex = info["engines"][:2]
        self.assertEqual((claude["path"], claude["logged_in"]), (str(self.claude), True))
        self.assertEqual([m["id"] for m in claude["models"]], ["haiku", "sonnet", "opus"])
        self.assertIs(codex["logged_in"], False)
        self.assertEqual([m["id"] for m in codex["models"]], [None, "petit"])  # modèle caché exclu
        self.assertEqual(info["overrides"], {"summaries": {"model": "gros-modèle"}, "notes.mystery": {"engine": "codex-cli"}})
        self.assertNotIn("secret@example.com", json.dumps(info))

    def test_apply_keeps_the_rest_and_frees_tasks(self):
        cleared = ai.apply(self.path, "claude-cli", "sonnet")
        data = json.loads(self.path.read_text())
        self.assertEqual(data["classify"], {"engine": "claude-cli", "command": str(self.claude), "args": None,
                                            "rules": "mes règles", "model": "sonnet"})
        self.assertEqual(sorted(cleared), ["notes.mystery.engine", "summaries.model"])
        self.assertTrue(data["notes"]["enabled"])
        self.assertEqual(ai.current(config.load(self.path))["model"], "sonnet")
        with self.assertRaises(ai.AiError):
            ai.apply(self.path, "command")  # commande obligatoire
        ai.apply(self.path, "none")
        self.assertEqual(ai.current(config.load(self.path))["engine"], "none")

    def test_engine_test_and_interactive(self):
        answers = iter(["1", "", "o"])  # Claude Code, modèle proposé (Haiku), essai
        with open(os.devnull, "w") as devnull:
            stdout, sys.stdout = sys.stdout, devnull
            try:
                code = ai.interactive(self.path, ask=lambda _q: next(answers))
            finally:
                sys.stdout = stdout
        self.assertEqual(code, 0)
        cfg = config.load(self.path)
        self.assertEqual((ai.current(cfg)["engine"], ai.current(cfg)["model"]), ("claude-cli", "haiku"))
        result = ai.test(cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "claude-haiku-test")


if __name__ == "__main__":
    unittest.main()
