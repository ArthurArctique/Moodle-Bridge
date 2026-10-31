"""Tests des parties multi-plateformes, de la connexion, des modèles, de l'assistant et du skill.

Aucun accès réseau, aucune tâche planifiée réelle, aucun modèle réel. Lancer :
python3 -I -m unittest discover tests
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mb import api, config, credentials, schedule, texts, util, wizard  # noqa: E402

try:
    from mb import llm
except ImportError:  # édition sans modèle de langage
    llm = None
try:
    from mb import skillgen
except ImportError:  # édition sans skill
    skillgen = None

SITE = "https://moodle.test"


def make_config(tmp, extra=None):
    path = Path(tmp) / ".moodle-bridge" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"site": SITE, "index": {"path": "Moodle.md"}, "texts": {"dir": "Textes Moodle"}, **(extra or {})}
    path.write_text(json.dumps(data), "utf-8")
    return config.load(path)


class PortableNamesTest(unittest.TestCase):
    def test_safe_name_is_valid_on_windows(self):
        self.assertEqual(util.safe_name('TD 3 : sujet <v2>?.pdf'), "TD 3 - sujet _v2__.pdf")
        self.assertEqual(util.safe_name("CON.txt"), "_CON.txt")
        self.assertEqual(util.safe_name("  fin.  "), "fin")

    def test_links_are_relative_under_the_index_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = Path(tmp) / "Moodle.md"
            self.assertEqual(texts.link_from(index_path, Path(tmp) / "ABC" / "TD 1.pdf"), "ABC/TD%201.pdf")
            outside = Path(tmp).parent / "ailleurs.pdf"
            self.assertTrue(texts.link_from(index_path, outside).startswith("file:"))


class ScheduleTest(unittest.TestCase):
    def test_windows_task_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp)
            xml = schedule.build_task_xml(cfg, 90, now=0)
        self.assertIn("<Interval>PT90M</Interval>", xml)
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", xml)
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertIn("sync", xml)

    def test_cron_schedule(self):
        self.assertEqual(schedule.cron_schedule(30), "*/30 * * * *")
        self.assertEqual(schedule.cron_schedule(120), "0 */2 * * *")

    def test_cron_install_replaces_only_its_own_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(Path(tmp) / "mes cours")
            written = []
            existing = ["0 * * * * autre-tache", f"* * * * * ancien {schedule.CRON_MARK}{schedule.label(cfg)}"]
            with mock.patch.object(schedule, "_crontab", return_value=existing), \
                    mock.patch.object(schedule, "_write_crontab", side_effect=written.append):
                schedule._cron_install(cfg, 120, "sync")
        lines = written[0]
        self.assertEqual(lines[0], "0 * * * * autre-tache")
        self.assertFalse(any("ancien" in line for line in lines))
        self.assertTrue(lines[1].startswith("0 */2 * * * "))
        self.assertTrue(lines[2].startswith("@reboot "))
        self.assertIn("'", lines[1])  # chemin avec espace : guillemets du shell


@unittest.skipIf(llm is None, "édition sans modèle de langage")
class EnginesTest(unittest.TestCase):
    def test_settings_inherit_from_classify_engine(self):
        base = {"engine": "codex-cli", "command": "/bin/codex", "model": "petit"}
        self.assertEqual(llm.settings_for({}, base)["command"], "/bin/codex")
        other = llm.settings_for({"engine": "claude-cli"}, base)
        self.assertEqual(other["engine"], "claude-cli")
        self.assertIsNone(other["command"])  # pas la commande d'un autre moteur

    def test_none_engine(self):
        self.assertIsNone(llm.make_engine({"engine": "none"}, "s"))

    def test_command_engine_reads_stdin(self):
        script = "import sys; print(sys.stdin.read().upper())"
        engine = llm.make_engine({"engine": "command", "command": [sys.executable, "-c", script]}, "consigne")
        self.assertIn("CONSIGNE", engine.ask("demande"))
        self.assertIn("DEMANDE", engine.ask("demande"))

    def test_claude_runs_without_tools(self):
        seen = {}

        def fake_run(args, prompt, timeout, cwd, what):
            seen["args"] = args
            return mock.Mock(stdout=json.dumps({"result": "{}", "modelUsage": {"m": {}}}))

        with mock.patch.object(llm, "_run", fake_run), mock.patch.object(llm, "_resolve", lambda c: c):
            engine = llm.make_engine({"engine": "claude-cli"}, "s")
            self.assertEqual(engine.ask("x"), "{}")
        self.assertIn("--tools", seen["args"])
        self.assertEqual(seen["args"][seen["args"].index("--tools") + 1], "")
        self.assertEqual(engine.models_used, {"m"})

    def test_codex_reads_last_message_file(self):
        def fake_run(args, prompt, timeout, cwd, what):
            self.assertIn("read-only", args)
            Path(args[args.index("-o") + 1]).write_text("réponse", "utf-8")
            return mock.Mock(stdout="")

        with mock.patch.object(llm, "_run", fake_run), mock.patch.object(llm, "_resolve", lambda c: c):
            engine = llm.make_engine({"engine": "codex-cli", "model": "petit"}, "s")
            self.assertEqual(engine.ask("x"), "réponse")


class CredentialsTest(unittest.TestCase):
    def _answer(self, payload):
        response = io.BytesIO(json.dumps(payload).encode())
        response.__enter__ = lambda self=response: self
        response.__exit__ = lambda *a: None
        return response

    def test_password_login_returns_token(self):
        token = "a" * 32
        with mock.patch("urllib.request.urlopen", return_value=self._answer({"token": token})):
            self.assertEqual(credentials.login_with_password(SITE, "u", "p"), token)

    def test_password_login_explains_sso(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=self._answer({"error": "x", "errorcode": "invalidlogin"})):
            with self.assertRaises(credentials.LoginError) as ctx:
                credentials.login_with_password(SITE, "u", "p")
        self.assertIn("navigateur", str(ctx.exception))

    def test_launch_payload(self):
        import base64
        token = "b" * 32
        signature = credentials.expected_signature(SITE, "abc")
        url = "moodlemobile://token=" + base64.b64encode(f"{signature}:::{token}".encode()).decode()
        self.assertEqual(credentials.parse_launch_payload(url)[:2], (signature, token))

    def test_environment_variable_wins(self):
        with mock.patch.dict(os.environ, {credentials.ENV_VAR: "c" * 32}):
            self.assertEqual(credentials.read_token("inutile"), "c" * 32)


class WizardTest(unittest.TestCase):
    PUBLIC = {"enablewebservices": 1, "enablemobilewebservice": 1, "typeoflogin": 1, "sitename": "Test",
              "wwwroot": SITE}

    def test_normalize_site(self):
        for raw in ("moodle.test", "https://moodle.test/", "https://moodle.test/my/",
                    "https://moodle.test/course/view.php", "https://moodle.test/login/index.php"):
            self.assertEqual(wizard.normalize_site(raw), SITE, raw)
        self.assertEqual(wizard.normalize_site("https://x.test/moodle/my/courses.php"), "https://x.test/moodle")

    def test_courses_config_keeps_existing_and_dedupes_codes(self):
        courses = [{"id": 1, "shortname": "ABC", "fullname": "A"}, {"id": 2, "shortname": "abc", "fullname": "B"},
                   {"id": 3, "shortname": "X/Y", "fullname": "C"}, {"id": 4, "shortname": "Z", "fullname": "D"}]
        existing = {"1": {"dest": "Perso", "code": "P"}}
        result = wizard.courses_config(courses, "mirror", existing, exclude=[4])
        self.assertEqual(result["1"], {"dest": "Perso", "code": "P"})
        self.assertEqual(result["2"]["code"], "abc")
        self.assertEqual(result["3"]["dest"], "X-Y")
        self.assertTrue(result["4"]["skip"])

    def test_answers_mode_waits_for_the_user_to_connect(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(api, "public_config", return_value=self.PUBLIC), \
                mock.patch.object(wizard, "check_token", return_value=None):
            done = wizard.run({"site": "moodle.test/my/", "env": tmp, "engine": "none"})
            data = json.loads((Path(tmp) / ".moodle-bridge" / "config.json").read_text("utf-8"))
        self.assertEqual(done["pending"], ["token"])
        self.assertIn("token --config", done["next"])
        self.assertEqual(data["site"], SITE)
        self.assertEqual(data["index"]["path"], "Moodle.md")
        self.assertEqual(data["default_mode"], "mirror")
        self.assertTrue(data["keychain_service"].startswith("moodle-bridge:"))

    def test_answers_mode_completes_after_connection(self):
        client = mock.Mock()
        client.call.side_effect = lambda fn, **kw: (
            {"userid": 7} if fn == "core_webservice_get_site_info"
            else [{"id": 11, "shortname": "ABC", "fullname": "Cours A"}, {"id": 12, "shortname": "DEF", "fullname": "B"}])
        calls = []
        actions = {"sync": lambda cfg: calls.append("sync"), "schedule": lambda cfg: calls.append("schedule")}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(api, "public_config", return_value=self.PUBLIC), \
                mock.patch.object(wizard, "check_token", return_value=("Étudiant", "Test")), \
                mock.patch.object(credentials, "read_token", return_value="d" * 32), \
                mock.patch.object(api, "Client", return_value=client):
            done = wizard.run({"site": SITE, "env": tmp, "engine": "none", "exclude_courses": [12],
                               "schedule": False, "menubar": False}, actions=actions)
            data = json.loads((Path(tmp) / ".moodle-bridge" / "config.json").read_text("utf-8"))
            ignore = (Path(tmp) / ".moodle-bridge" / ".gitignore").exists()
        self.assertEqual(calls, ["sync"])
        self.assertEqual(done["pending"], [])
        self.assertEqual(data["courses"]["11"]["dest"], "ABC")
        self.assertEqual(data["courses"]["11"]["mode"], "mirror")
        self.assertTrue(data["courses"]["12"]["skip"])
        self.assertTrue(ignore)

    def test_site_without_mobile_service_is_refused(self):
        closed = {**self.PUBLIC, "enablemobilewebservice": 0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(api, "public_config", return_value=closed):
            with self.assertRaises(wizard.Abort):
                wizard.run({"site": SITE, "env": tmp})


@unittest.skipIf(skillgen is None, "édition sans skill")
class SkillTest(unittest.TestCase):
    def test_render_nested_conditions(self):
        template = "a<!-- if x -->b<!-- if y -->c<!-- endif -->d<!-- endif --><!-- if !y -->e<!-- endif -->{{V}}"
        self.assertEqual(skillgen.render(template, {"x": True, "y": False}, {"V": "!"}), "abde!")
        self.assertEqual(skillgen.render(template, {"x": True, "y": True}, {"V": "!"}), "abcd!")
        with self.assertRaises(skillgen.SkillError):
            skillgen.render("{{INCONNU}}", {}, {})

    def test_template_renders_without_leftovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = skillgen.render(skillgen.TEMPLATE.read_text("utf-8"), skillgen.flags(make_config(tmp)),
                                   skillgen.values(make_config(tmp)))
        self.assertNotIn("{{", text)
        self.assertNotIn("<!-- if", text)
        self.assertNotIn("<!-- endif", text)
        self.assertIn("moodle-bridge-skill: installed", text)

    def test_install_replaces_bootstrap_but_not_foreign_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(Path(tmp) / "env")
            dest = Path(tmp) / "skills" / "moodle-bridge"
            dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text((skillgen.SKILL_SRC / "SKILL.md").read_text("utf-8"), "utf-8")
            skillgen.install(cfg, dest)
            self.assertEqual(skillgen.kind_of(dest), "installed")
            self.assertTrue((dest / "references" / "fonctionnement.md").exists())
            skillgen.install(cfg, dest)  # réinstallation

            foreign = Path(tmp) / "skills" / "autre"
            (foreign / "scripts").mkdir(parents=True)
            (foreign / "SKILL.md").write_text("autre", "utf-8")
            (foreign / "scripts" / "moodle_bridge.py").write_text("", "utf-8")
            with self.assertRaises(skillgen.SkillError):
                skillgen.install(cfg, foreign)
            self.assertEqual((foreign / "SKILL.md").read_text("utf-8"), "autre")


if __name__ == "__main__":
    unittest.main()
