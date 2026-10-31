"""Tests du relevé des notes : faux Moodle, faux modèle, faux classeur ; aucun accès réseau.

Lancer : python3 -I -m unittest discover <skill>/tests
"""

import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mb import config, notes  # noqa: E402

SITE = "https://moodle.test"


class GradesClient:
    """Moodle factice : éléments de note modifiables entre deux passages."""

    def __init__(self):
        self.items = {10: [self.item(1, "Examen final", None), self.item(2, "Q01 entraînement", 12, module="quiz"),
                           {"id": 3, "itemtype": "course", "itemname": None, "graderaw": 14, "grademax": 20}]}

    @staticmethod
    def item(itemid, name, raw, maximum=20, module="assign", feedback=""):
        return {"id": itemid, "itemname": name, "itemtype": "mod", "itemmodule": module, "graderaw": raw,
                "gradeformatted": "-" if raw is None else f"{raw:.2f}".replace(".", ","), "grademin": 0,
                "grademax": maximum, "scaleid": None, "feedback": feedback, "percentageformatted": None}

    def set(self, itemid, raw, **extra):
        self.items[10] = [i if i["id"] != itemid else self.item(itemid, i["itemname"], raw, **extra)
                          for i in self.items[10]]

    def call(self, function, **params):
        if function == "core_webservice_get_site_info":
            return {"userid": 7}
        if function == "core_enrol_get_users_courses":
            return [{"id": 10, "shortname": "M1_TST", "fullname": "TST - Cours de test"}]
        if function == "gradereport_user_get_grade_items":
            return {"usergrades": [{"gradeitems": self.items.get(params["courseid"], [])}]}
        raise AssertionError(function)


class FakeEngine:
    model = "faux-modele"

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeWriter:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"status": "written", "cell": "H7", "previous": 17, "backup": "/x/b.xlsx"}

    def __call__(self, python, request):
        self.calls.append(request)
        return dict(self.result)


GOOD = json.dumps({"mysteries": [{"id": "1", "emoji": "🏆", "color": "#16a34a", "dot": "🟢",
                                  "teaser": "Une surprise t'attend"}]})


def make_env(mapped=True):
    tmp = Path(tempfile.mkdtemp(prefix="mb-notes-"))
    (tmp / ".moodle-bridge").mkdir()
    workbook = tmp / "notes.xlsx"
    workbook.write_bytes(b"")
    data = {"site": SITE, "courses": {"10": {"dest": "Cours/TST", "code": "TST"}},
            "notes": {"enabled": True, "workbook": {
                "path": "notes.xlsx", "python": "/bin/sh",
                "map": {"10/1": {"element": "TST", "note": 1}} if mapped else {}}}}
    path = tmp / ".moodle-bridge" / "config.json"
    path.write_text(json.dumps(data))
    cfg = config.load(path)
    cfg.cache_dir = tmp / "cache"
    return tmp, cfg


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._notify = notes.util.notify
        notes.util.notify = lambda title, message, subtitle=None: self.sent.append((title, subtitle, message))
        self.tmp, self.cfg = make_env()
        self.client = GradesClient()
        self.writer = FakeWriter()

    def tearDown(self):
        notes.util.notify = self._notify

    def run_once(self, engine=None, **kwargs):
        return notes.run(self.cfg, lambda: self.client, lambda *a, **k: None, engine=engine,
                         runner=self.writer, **kwargs)

    def view(self):
        return json.loads(notes.view_path(self.cfg).read_text("utf-8"))

    def reveal(self, key):
        folder = notes.requests_dir(self.cfg)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key.replace('/', '-')}.json").write_text(json.dumps({"key": key}))

    def test_baseline_then_mystery_without_value(self):
        self.client.set(1, 15.5)
        report = self.run_once()
        self.assertEqual(report["new"], [])  # note déjà là à l'installation : seulement mémorisée
        self.assertEqual(notes.load_state(self.cfg)["items"]["10/1"]["status"], "known")
        self.assertNotIn("10/2", notes.load_state(self.cfg)["items"])  # quiz exclu
        self.assertEqual(self.sent, [])

        self.client.set(1, 13.25)
        engine = FakeEngine(GOOD)
        report = self.run_once(engine=engine)
        self.assertEqual(report["new"], ["10/1"])
        self.assertIn("13,25 / 20", engine.prompts[0])  # le modèle voit la note pour choisir
        row = self.view()["grades"][0]
        self.assertEqual((row["status"], row["event"], row["emoji"], row["color"]), ("mystery", "changed", "🏆", "#16A34A"))
        self.assertNotIn("grade", row)
        raw = notes.view_path(self.cfg).read_text("utf-8")
        self.assertNotIn("13,25", raw)
        self.assertNotIn("13.25", raw)
        title, subtitle, message = self.sent[0]
        self.assertIn("🏆", title)
        self.assertEqual(subtitle, "🟢 TST · Examen final")
        self.assertNotIn("13", title + subtitle + message)
        self.assertEqual(self.writer.calls, [])  # rien dans le classeur avant la découverte

    def test_sealed_envelope_opens_only_mysteries(self):
        self.run_once()
        self.client.set(1, 13.5)
        self.run_once(engine=FakeEngine(GOOD))
        raw = notes.sealed_path(self.cfg).read_text("utf-8")
        self.assertNotIn("13,5", raw)
        self.assertNotIn("13.5", raw)
        sealed = json.loads(raw)["sealed"]
        opened = json.loads(notes.base64.b64decode(sealed["10/1"]).decode("utf-8"))
        self.assertEqual((opened["grade"], opened["ratio"]), ("13,5 / 20", 0.675))
        self.assertEqual(opened["workbook_tone"], "wait")  # report à faire par la tâche après le clic
        self.reveal("10/1")
        self.run_once()
        self.assertEqual(json.loads(notes.sealed_path(self.cfg).read_text("utf-8"))["sealed"], {})
        self.assertEqual(self.view()["grades"][0]["grade"], "13,5 / 20")

    def test_reveal_writes_workbook_once(self):
        self.run_once()
        self.client.set(1, 16, maximum=20, feedback="<p>Bon <b>travail</b></p>")
        self.run_once(engine=FakeEngine(GOOD))
        self.reveal("10/1")
        report = self.run_once()
        self.assertEqual(report["revealed"], ["10/1"])
        row = self.view()["grades"][0]
        self.assertEqual((row["status"], row["grade"]), ("revealed", "16 / 20"))
        self.assertIn("**travail**", row["feedback"])
        self.assertIn("remplace 17", row["workbook"])
        self.assertEqual(self.writer.calls, [{"path": str(self.cfg.env_root / "notes.xlsx"), "sheet": "Notes",
                                              "element": "TST", "note": 1, "value": 16.0}])
        self.run_once()
        self.assertEqual(len(self.writer.calls), 1)  # report terminé : plus jamais réécrit

    def test_out_of_20_converted_and_locked_retried(self):
        self.run_once()
        self.client.set(1, 7, maximum=10)
        self.run_once()
        self.assertEqual(self.view()["grades"][0]["emoji"], "🎁")  # sans modèle : mystère par défaut
        self.reveal("10/1")
        lock = self.tmp / "~$notes.xlsx"
        lock.write_text("")
        self.run_once()
        row = self.view()["grades"][0]
        self.assertEqual((row["grade"], row["on20"], row["workbook_tone"]), ("7 / 10", 14.0, "wait"))
        self.assertEqual(self.writer.calls, [])
        lock.unlink()
        self.run_once()
        self.assertEqual(self.writer.calls[0]["value"], 14.0)

    def test_unmapped_then_mapped(self):
        self.tmp, self.cfg = make_env(mapped=False)
        self.run_once()
        self.client.set(1, 11)
        self.run_once()
        self.reveal("10/1")
        self.run_once()
        self.assertEqual(self.view()["grades"][0]["workbook_tone"], "info")
        self.cfg.data["notes"]["workbook"]["map"] = {"10/1": {"element": "TST", "note": 2}}
        self.run_once()
        self.assertEqual(self.writer.calls[0]["note"], 2)

    def test_conflict_between_items(self):
        self.cfg.data["notes"]["workbook"]["map"]["10/4"] = {"element": "TST", "note": 1}
        self.client.items[10].append(GradesClient.item(4, "Rattrapage", None))
        self.run_once()
        self.client.set(1, 12)
        self.client.set(4, 14)
        self.run_once()
        self.reveal("10/1")
        self.reveal("10/4")
        self.run_once()
        states = sorted(r["workbook_tone"] for r in self.view()["grades"])
        self.assertEqual(states, ["error", "ok"])
        self.assertEqual(len(self.writer.calls), 1)

    def test_model_failure_falls_back_and_retries(self):
        self.run_once()
        self.client.set(1, 9)
        self.run_once(engine=FakeEngine(notes.classify.ClassifierError("claude indisponible")))
        self.assertEqual(self.view()["grades"][0]["emoji"], "🎁")
        self.assertEqual(len(self.sent), 1)  # prévenu quand même
        self.run_once(engine=FakeEngine(GOOD))
        self.assertEqual(self.view()["grades"][0]["emoji"], "🏆")
        self.assertEqual(len(self.sent), 1)  # pas de seconde notification

    def test_reveal_works_offline(self):
        self.run_once()
        self.client.set(1, 10)
        self.run_once()
        self.reveal("10/1")

        def offline():
            raise urllib.error.URLError("hors ligne")
        with self.assertRaises(urllib.error.URLError):
            notes.run(self.cfg, offline, lambda *a, **k: None, runner=self.writer,
                      describe=lambda exc: ("Moodle injoignable", "network"))
        view = self.view()
        self.assertEqual((view["grades"][0]["status"], view["last_error"]), ("revealed", "Moodle injoignable"))
        self.assertEqual(len(self.writer.calls), 1)

    def test_clean_mystery(self):
        self.assertIsNone(notes.clean_mystery({"emoji": "1️⃣"}))
        self.assertIsNone(notes.clean_mystery({"emoji": "A+"}))
        self.assertIsNone(notes.clean_mystery({"emoji": "🎉 🎉"}))
        mystery = notes.clean_mystery({"emoji": "🌧️", "color": "#3366ff", "dot": "rouge", "teaser": "Note : 8 sur 20"})
        self.assertEqual((mystery["color"], mystery["dot"], mystery["teaser"]), ("#3366FF", "🔵", None))
        self.assertEqual(notes.clean_mystery({"emoji": "🫖", "dot": "🟣"})["color"], notes.DOT_COLORS["🟣"])


VENV_PYTHON = notes.VENV / "bin" / "python3"


@unittest.skipUnless(VENV_PYTHON.exists(), "openpyxl absent (notes setup)")
class WorkbookWriterTests(unittest.TestCase):
    """Le vrai notes_workbook.py, sur un petit classeur créé pour le test."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="mb-xlsx-"))
        self.path = self.dir / "notes.xlsx"
        subprocess.run([str(VENV_PYTHON), "-I", "-c", f"""
import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Notes"
ws.append(["Titre"]); ws.append([])
ws.append(["Semestre", "Élément", "Note 1", "Coef. 1", "Note 2", "Moyenne"])
ws.append([1, "AAA", 17, 2, None, "=AVERAGE(C4,E4)"])
ws.append([1, "BBB", None, 1, None, "=AVERAGE(C5,E5)"])
dv = DataValidation(type="decimal", operator="between", formula1="0", formula2="20"); ws.add_data_validation(dv); dv.add("C4:E5")
ws.merge_cells("A1:F1"); wb.create_sheet("Synthèse")["A1"] = "=AVERAGE('Saisie notes'!F4:F5)"
wb.save({str(self.path)!r})
"""], check=True)

    def write(self, element, note, value):
        return notes.run_writer(VENV_PYTHON, {"path": str(self.path), "sheet": "Notes",
                                              "element": element, "note": note, "value": value})

    def test_write_backup_and_integrity(self):
        result = self.write("AAA", 1, 15.456)
        self.assertEqual((result["status"], result["cell"], result["previous"]), ("written", "C4", 17))
        self.assertTrue(Path(result["backup"]).exists())
        self.assertEqual(self.write("AAA", 1, 15.46)["status"], "unchanged")
        self.assertEqual(self.write("BBB", 2, 12)["cell"], "E5")
        self.assertEqual(self.write("CCC", 1, 12)["status"], "error")
        self.assertEqual(self.write("AAA", 3, 12)["status"], "error")
        (self.dir / "~$notes.xlsx").write_text("")
        self.assertEqual(self.write("BBB", 1, 9)["status"], "locked")
        check = subprocess.run([str(VENV_PYTHON), "-I", "-c", f"""
import openpyxl
wb = openpyxl.load_workbook({str(self.path)!r}); ws = wb["Notes"]
print(ws["C4"].value, ws["E5"].value, ws["C5"].value, ws["F4"].value, len(ws.data_validations.dataValidation),
      wb.calculation.fullCalcOnLoad, wb["Synthèse"]["A1"].value)
"""], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(check, ["15.46", "12", "None", "=AVERAGE(C4,E4)", "1", "True",
                                 "=AVERAGE('Saisie", "notes'!F4:F5)"])


if __name__ == "__main__":
    unittest.main()
