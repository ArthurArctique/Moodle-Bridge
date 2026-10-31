"""Tests du pont Moodle avec un faux serveur : aucun accès réseau, aucun modèle réel.

Lancer : python3 -I -m unittest discover <skill>/tests
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mb import api, classify, config, fetch, index, credentials, sync, vault  # noqa: E402

try:
    from mb import snapshot  # noqa: E402
except ImportError:  # édition sans application de barre des menus
    snapshot = None

T0 = 1_790_000_000
fetch.PAUSE_BETWEEN_COURSES = 0


class FakeClient:
    """Moodle factice : contenus modifiables entre deux passages, compteur de téléchargements."""

    site = "https://moodle.test"

    def __init__(self):
        self.files = {}  # fileurl -> bytes
        self.downloads = 0
        self.sections = []
        self.assign_attachments = []
        self.assign_intro = ""
        self.pages = []
        self.discussions = [{"name": "Salle changée", "discussion": 42, "created": T0}]

    def add_file(self, cmid, name, data, section="TP 1", modname="resource", module=None,
                 filepath="/", mimetype="application/pdf", when=T0):
        url = f"{self.site}/webservice/pluginfile.php/{cmid}{filepath}{name}"
        self.files[url] = data
        entry = {"type": "file", "filename": name, "filepath": filepath, "filesize": len(data),
                 "timemodified": when, "mimetype": mimetype, "fileurl": url}
        for sec in self.sections:
            if sec["name"] == section:
                break
        else:
            sec = {"name": section, "section": len(self.sections), "modules": []}
            self.sections.append(sec)
        for mod in sec["modules"]:
            if mod["id"] == cmid:
                mod["contents"] = [c for c in mod["contents"] if c["fileurl"] != url] + [entry]
                return
        sec["modules"].append({"id": cmid, "modname": modname, "name": module or name,
                               "url": f"{self.site}/mod/{modname}/view.php?id={cmid}", "contents": [entry]})

    def call(self, function, **params):
        if function == "core_webservice_get_site_info":
            return {"userid": 7, "fullname": "Étudiant Test", "sitename": "Moodle test"}
        if function == "core_enrol_get_users_courses":
            return [{"id": 10, "shortname": "M1_TST", "fullname": "TST - Cours de test"},
                    {"id": 99, "shortname": "NEW", "fullname": "Cours inconnu"}]
        if function == "core_course_get_contents":
            return self.sections if params["courseid"] == 10 else []
        if function == "mod_assign_get_assignments":
            return {"courses": [{"id": 10, "assignments": [
                {"cmid": 500, "name": "Rendu projet", "duedate": int(time.time()) + 86400,
                 "intro": self.assign_intro, "timemodified": T0,
                 "introattachments": self.assign_attachments}]}]}
        if function == "mod_page_get_pages_by_courses":
            return {"pages": self.pages}
        if function == "core_calendar_get_calendar_upcoming_view":
            return {"events": [{"id": 1, "name": "Rendu projet doit être rendu",
                                "timestart": int(time.time()) + 86400, "course": {"id": 10, "shortname": "M1_TST"},
                                "url": f"{self.site}/mod/assign/view.php?id=500"}]}
        if function == "mod_forum_get_forums_by_courses":
            return [{"id": 3, "course": 10, "type": "news"}]
        if function == "mod_forum_get_forum_discussions":
            return {"discussions": self.discussions[:params.get("perpage") or None]}
        raise api.MoodleError("unknown", function)

    def download(self, fileurl, dest, expected_size=None):
        self.downloads += 1
        data = self.files[fileurl]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        return dest


class FakeClassifier:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.answer(prompt) if callable(self.answer) else self.answer


def make_env(courses=None, **extra):
    tmp = Path(tempfile.mkdtemp(prefix="mb-test-"))
    (tmp / ".moodle-bridge").mkdir()
    (tmp / "Cours" / "TST" / "TD" / "TD03").mkdir(parents=True)
    data = {
        "site": FakeClient.site,
        "courses": courses or {"10": {"dest": "Cours/TST", "code": "TST", "mode": "classify"}},
        "classify": {"engine": "none", "rules": "CM/CMNN, TD/TDNN, TP/TPNN"},
        "index": {"path": "Vault/Moodle.md"},
        **extra,
    }
    path = tmp / ".moodle-bridge" / "config.json"
    path.write_text(json.dumps(data))
    cfg = config.load(path)
    cfg.cache_dir = tmp / "cache"
    return tmp, cfg


def quiet(*_args, **_kwargs):
    pass


class ClassifyTests(unittest.TestCase):
    def test_normalize_rejects_escapes(self):
        self.assertEqual(classify.normalize_dest("./TD/TD03/"), "TD/TD03")
        for bad in ("../x", "/abs", "~/x", "TD/../../x", ".git", "", None, 3):
            self.assertIsNone(classify.normalize_dest(bad), bad)

    def test_validate_dest(self):
        tmp, cfg = make_env()
        root = tmp / "Cours" / "TST"
        patterns = config.DEFAULTS["classify"]["new_dir_patterns"]
        self.assertEqual(classify.validate_dest("TD/TD03", root, patterns, "_a_classer"), "TD/TD03")
        self.assertEqual(classify.validate_dest("CM/CM07", root, patterns, "_a_classer"), "CM/CM07")
        self.assertIsNone(classify.validate_dest("CM/TD07", root, patterns, "_a_classer"))
        self.assertIsNone(classify.validate_dest("Divers", root, patterns, "_a_classer"))
        self.assertIsNone(classify.validate_dest("_a_classer", root, patterns, "_a_classer"))

    def test_parse_answer_tolerates_fences(self):
        text = 'Voici :\n```json\n{"placements": [{"id": "1", "dest": "TP/TP01", "confidence": 0.9}]}\n```'
        self.assertEqual(classify.parse_answer(text)[0]["dest"], "TP/TP01")
        with self.assertRaises(classify.ClassifierError):
            classify.parse_answer("pas de json")

    def test_dest_dir_strips_folder_subpath(self):
        root = Path("/r")
        self.assertEqual(classify.dest_dir_for(root, "/r/TD/TD03/data/a.csv", "/data/"), "TD/TD03")
        self.assertEqual(classify.dest_dir_for(root, "/r/TD/TD03/a.pdf", "/"), "TD/TD03")
        self.assertIsNone(classify.dest_dir_for(root, "/autre/a.pdf", "/"))


class TokenTests(unittest.TestCase):
    def test_parse_launch_payload(self):
        import base64
        sig = credentials.expected_signature("https://moodle.test", "abc")
        payload = base64.b64encode(f"{sig}:::{'a' * 32}:::{'x' * 64}".encode()).decode()
        for text in (f"moodlemobile://token={payload}", f"moodlemobile://token={payload.replace('=', '%3D')}", payload):
            got_sig, got_token, problem = credentials.parse_launch_payload(text)
            self.assertIsNone(problem)
            self.assertEqual((got_sig, got_token), (sig, "a" * 32))
        self.assertIsNotNone(credentials.parse_launch_payload(credentials.launch_url("https://moodle.test", "abc"))[2])
        self.assertIsNotNone(credentials.parse_launch_payload("bonjour")[2])


class SyncTests(unittest.TestCase):
    def test_full_cycle(self):
        tmp, cfg = make_env()
        client = FakeClient()
        # Déjà présents localement (même contenu, autre nom) : ne doivent pas être recopiés
        (tmp / "Cours/TST/TD/TD03/sujet_local.pdf").write_bytes(b"SUJET-TD3")
        (tmp / "Cours/TST/TD/TD03/annexe.pdf").write_bytes(b"ANNEXE-TD3")
        client.add_file(101, "sujet.pdf", b"SUJET-TD3", section="TP du 1er octobre")
        client.add_file(101, "annexe.pdf", b"ANNEXE-TD3", section="TP du 1er octobre")
        # Même activité que deux fichiers déjà rangés au même endroit : rangé à côté (règle des voisins)
        client.add_file(101, "donnees.csv", b"a,b\n1,2\n", section="TP du 1er octobre", mimetype="text/csv")
        # Un seul voisin dans la section : pas de règle, le « modèle » décide (ici sans assez de confiance)
        (tmp / "Cours/TST/TD/TD03").mkdir(exist_ok=True)
        client.add_file(106, "autre_seance.pdf", b"AUTRE", section="TP du 1er octobre")
        # Rangé par le « modèle »
        client.add_file(102, "CM7.pdf", b"CM7", section="CM")
        # Confiance insuffisante : boîte d'attente
        client.add_file(103, "mystere.pdf", b"???", section="Divers")
        # Vidéo : listée seulement ; page : ignorée
        client.add_file(104, "cours.mp4", b"VIDEO", section="CM", mimetype="video/mp4")
        client.add_file(105, "index.html", b"<p>", section="CM", modname="page")
        client.assign_attachments = [{"filename": "consignes.pdf", "filepath": "/", "filesize": 4,
                                      "timemodified": T0, "mimetype": "application/pdf",
                                      "fileurl": f"{client.site}/webservice/pluginfile.php/500/consignes.pdf"}]
        client.files[client.assign_attachments[0]["fileurl"]] = b"CONS"

        def answer(prompt):
            placements = []
            for line in prompt.splitlines():
                if line.startswith("- id="):
                    ident = line.split("|")[0].split("=")[1].strip()
                    if "CM7.pdf" in line:
                        placements.append({"id": ident, "dest": "CM/CM07", "confidence": 0.95, "reason": "CM7"})
                    elif "consignes.pdf" in line:
                        placements.append({"id": ident, "dest": "Projet", "confidence": 0.9, "reason": "devoir"})
                    else:
                        placements.append({"id": ident, "dest": "TP/TP09", "confidence": 0.4, "reason": "?"})
            return json.dumps({"placements": placements})

        classifier = FakeClassifier(answer)

        # Simulation : rien n'est écrit dans l'environnement hors plan.json
        report = sync.run(cfg, client, classifier, dry_run=True, log=quiet)
        self.assertTrue(cfg.plan_path.exists())
        self.assertFalse(cfg.state_path.exists())
        self.assertFalse((tmp / "Cours/TST/CM").exists())
        self.assertEqual(report["decisions"]["10/101/sujet.pdf"]["action"], "present")
        downloads_after_dry_run = client.downloads
        prompts_after_dry_run = len(classifier.prompts)

        # Passage réel : réutilise le cache et les décisions de la simulation
        report = sync.run(cfg, client, classifier, dry_run=False, log=quiet)
        index.write(cfg, sync.load_state(cfg.state_path))
        self.assertEqual(client.downloads, downloads_after_dry_run)
        self.assertEqual(len(classifier.prompts), prompts_after_dry_run)  # décisions reprises du plan
        self.assertFalse(cfg.plan_path.exists())
        root = tmp / "Cours/TST"
        self.assertFalse((root / "TD/TD03/sujet.pdf").exists())
        self.assertEqual((root / "TD/TD03/donnees.csv").read_bytes(), b"a,b\n1,2\n")
        self.assertEqual((root / "CM/CM07/CM7.pdf").read_bytes(), b"CM7")
        self.assertEqual((root / "Projet/consignes.pdf").read_bytes(), b"CONS")
        self.assertEqual((root / "_a_classer/mystere.pdf").read_bytes(), b"???")
        self.assertTrue((root / "_a_classer/autre_seance.pdf").exists())
        self.assertFalse((root / "CM/cours.mp4").exists())
        state = sync.load_state(cfg.state_path)
        self.assertEqual(state["items"]["10/104/cours.mp4"]["status"], "listed")
        self.assertNotIn("10/105/index.html", state["items"])
        self.assertEqual(report["unconfigured"], {99: "NEW"})

        text = (tmp / "Vault/Moodle.md").read_text()
        for expected in ("## À venir", "Rendu projet doit être rendu", "## À classer", "mystere.pdf",
                         "🎬", "Salle changée", "### TST — TST - Cours de test", "## Cours non configurés"):
            self.assertIn(expected, text)
        self.assertNotIn("wstoken", text)

        # Deuxième passage : rien à télécharger
        downloads = client.downloads
        report = sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(client.downloads, downloads)
        self.assertEqual(report["new"] + report["updated"] + report["inbox"], [])

        # Nouvelle version Moodle d'un fichier rangé : rangée à côté, l'original intact
        client.add_file(102, "CM7.pdf", b"CM7-v2", section="CM", when=T0 + 86400)
        report = sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(report["updated"], ["10/102/CM7.pdf"])
        self.assertEqual((root / "CM/CM07/CM7.pdf").read_bytes(), b"CM7")
        versions = [p.name for p in (root / "CM/CM07").iterdir() if "(Moodle" in p.name]
        self.assertEqual(len(versions), 1)

        # Rangement manuel depuis la boîte d'attente : suivi par empreinte, jamais retéléchargé
        (root / "TP/TP02").mkdir(parents=True)
        os.rename(root / "_a_classer/mystere.pdf", root / "TP/TP02/mystere.pdf")
        report = sync.run(cfg, client, classifier, log=quiet)
        entry = sync.load_state(cfg.state_path)["items"]["10/103/mystere.pdf"]
        self.assertEqual((entry["status"], entry["dest_dir"]), ("placed", "TP/TP02"))

        # Suppression locale : marquée, pas retéléchargée
        (root / "Projet/consignes.pdf").unlink()
        downloads = client.downloads
        sync.run(cfg, client, classifier, log=quiet)
        sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(client.downloads, downloads)
        self.assertEqual(sync.load_state(cfg.state_path)["items"]["10/500/consignes.pdf"]["status"], "missing")

    def test_collision_never_overwrites(self):
        tmp, cfg = make_env()
        local = sync.LocalFiles(tmp, {})
        target_dir = tmp / "Cours/TST/TD/TD03"
        (target_dir / "a.pdf").write_bytes(b"version locale annotee")
        src = tmp / "src.pdf"
        src.write_bytes(b"version moodle")
        digest = sync.util.sha256_file(src)
        path, how = sync.place(src, target_dir, "a.pdf", digest, T0, local)
        self.assertEqual(how, "copied")
        self.assertNotEqual(path.name, "a.pdf")
        self.assertEqual((target_dir / "a.pdf").read_bytes(), b"version locale annotee")
        again, how = sync.place(src, target_dir, "a.pdf", digest, T0, local)
        self.assertEqual((again, how), (path, "present"))

    def test_classifier_failure_goes_to_inbox(self):
        tmp, cfg = make_env()
        client = FakeClient()
        client.add_file(201, "x.pdf", b"X", section="Inconnue")

        class Broken:
            def ask(self, prompt):
                raise classify.ClassifierError("hors ligne")

        report = sync.run(cfg, client, Broken(), log=quiet)
        self.assertEqual(report["inbox"], ["10/201/x.pdf"])
        self.assertEqual(report["classifier_error"], "hors ligne")

    def test_routes_and_zotero(self):
        tmp, cfg = make_env(
            courses={"10": {"dest": "Cours/TST", "code": "TST", "routes": [
                {"sections": r"^TD\d+-", "dest": "Cours/XYZ", "code": "XYZ"}]}},
            zotero={"enabled": True, "staging": ".moodle-bridge/a_importer_zotero", "search_roots": ["Zot"]})
        (tmp / "Zot/Cours").mkdir(parents=True)
        (tmp / "Zot/Cours/deja.pdf").write_bytes(b"DEJA")
        client = FakeClient()
        client.add_file(401, "XYZ_TP1.pdf", b"IP", section="TD1- Présentation")
        client.add_file(402, "CM1_diapos.pdf", b"CM1", section="CM")
        client.add_file(403, "CM0.pdf", b"DEJA", section="CM")

        def answer(prompt):
            placements = []
            for line in prompt.splitlines():
                if line.startswith("- id="):
                    ident = line.split("|")[0].split("=")[1].strip()
                    dest = "TP/TP01" if "XYZ_TP1" in line else "@zotero"
                    placements.append({"id": ident, "dest": dest, "confidence": 0.9})
            return json.dumps({"placements": placements})

        classifier = FakeClassifier(answer)
        sync.run(cfg, client, classifier, log=quiet)
        staged = tmp / ".moodle-bridge/a_importer_zotero/TST/CM1_diapos.pdf"
        self.assertTrue((tmp / "Cours/XYZ/TP/TP01/XYZ_TP1.pdf").exists())
        self.assertTrue(staged.exists())
        self.assertFalse((tmp / "Cours/TST/CM").exists())
        items = sync.load_state(cfg.state_path)["items"]
        self.assertEqual(items["10/403/CM0.pdf"]["status"], "in_zotero")
        self.assertEqual(items["10/402/CM1_diapos.pdf"]["status"], "zotero_pending")
        self.assertTrue(any("Code : XYZ" in p for p in classifier.prompts))
        self.assertTrue(all('"@zotero"' in p for p in classifier.prompts))

        # Import fait par l'agent : retrouvé par empreinte, copie temporaire supprimée
        (tmp / "Zot/Cours/Mon CM1.pdf").write_bytes(b"CM1")
        report = sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(report["imported"], ["10/402/CM1_diapos.pdf"])
        self.assertFalse(staged.exists())
        text = index.render(cfg, sync.load_state(cfg.state_path))
        self.assertIn("## À importer dans Zotero", text)
        self.assertIn("📚", text)
        self.assertIn("Cours/XYZ/TP/TP01", text)

    def test_local_course_pdf_still_goes_to_zotero(self):
        tmp, cfg = make_env(zotero={"enabled": True, "staging": "stage", "search_roots": ["Zot"]})
        (tmp / "Zot").mkdir()
        cm_dir = tmp / "Cours/TST/CM/CM06"
        cm_dir.mkdir(parents=True)
        (cm_dir / "CM6.pdf").write_bytes(b"CM6")          # support de cours déposé à la main
        (tmp / "Cours/TST/TD/TD03/sujet.pdf").write_bytes(b"SUJET")  # sujet de TD déjà rangé
        client = FakeClient()
        client.add_file(601, "CM6.pdf", b"CM6", section="CM")
        client.add_file(602, "sujet.pdf", b"SUJET", section="TD")

        def answer(prompt):
            out = []
            for line in prompt.splitlines():
                if line.startswith("- id="):
                    ident = line.split("|")[0].split("=")[1].strip()
                    out.append({"id": ident, "dest": "@zotero" if "CM6" in line else "TD/TD03", "confidence": 0.9})
            return json.dumps({"placements": out})

        sync.run(cfg, client, FakeClassifier(answer), log=quiet)
        items = sync.load_state(cfg.state_path)["items"]
        cm6, sujet = items["10/601/CM6.pdf"], items["10/602/sujet.pdf"]
        self.assertEqual(cm6["status"], "zotero_pending")
        self.assertEqual(cm6["local_copy"], "Cours/TST/CM/CM06/CM6.pdf")
        self.assertTrue((tmp / "stage/TST/CM6.pdf").exists())
        self.assertEqual((cm_dir / "CM6.pdf").read_bytes(), b"CM6")  # copie locale intacte
        self.assertEqual(sujet["status"], "present")
        self.assertIn("une copie est aussi dans `Cours/TST/CM/CM06`",
                      index.render(cfg, sync.load_state(cfg.state_path)))

    def test_mirror_mode(self):
        tmp, cfg = make_env(courses={"10": {"dest": "Admin/Moodle", "mode": "mirror", "code": "INFO"}})
        client = FakeClient()
        client.add_file(301, "convention.pdf", b"C", section="Procédure : convention")
        sync.run(cfg, client, None, log=quiet)
        self.assertTrue((tmp / "Admin/Moodle/Procédure - convention/convention.pdf").exists())

    def test_lock(self):
        tmp, cfg = make_env()
        with sync.Lock(cfg.lock_path):
            with self.assertRaises(sync.AlreadyRunning):
                with sync.Lock(cfg.lock_path):
                    pass
        self.assertFalse(cfg.lock_path.exists())


class IndexTests(unittest.TestCase):
    def test_recent_announcements_on_top(self):
        tmp, cfg = make_env()
        now = int(time.time())
        state = sync.empty_state()
        state["remote"] = {"courses": [], "announcements": [
            {"courseid": 10, "course": "M1_TST", "name": "Report des CC", "time": now - 3600, "url": "u"},
            {"courseid": 10, "course": "M1_TST", "name": "Vieille annonce", "time": now - 30 * 86400, "url": "v"}]}
        text = index.render(cfg, state, now)
        top = text.split("## Nouveaux fichiers")[0]
        self.assertIn("## Annonces récentes", top)
        self.assertIn("Report des CC", top)
        self.assertNotIn("Vieille annonce", top)


class TextTests(unittest.TestCase):
    MCC_V1 = "<p><strong>CC1</strong> TP noté</p><p>Formule de calcul : (CC1 + 2*CC2) / 3</p>"
    MCC_V2 = "<p><strong>CC1</strong> TP noté</p><p>Formule de calcul : (CC1 + CC2) / 2</p>"

    def test_html_to_md(self):
        from mb.texts import html_to_md
        md = html_to_md(
            '<h4>Évaluation</h4><p>Note = (2*CC1 + CC2) / 3</p>'
            '<ul><li><strong>CC1</strong> : TP noté</li><li>CC2 : <a href="https://moodle.test/webservice/'
            'pluginfile.php/1/x.pdf?token=SECRET&amp;forcedownload=1">sujet</a></li></ul>'
            '<p># pas un titre</p><pre id="vide"></pre><p><a href="javascript:void(0)">rien</a></p>'
            '<table><tr><th>Épreuve</th><th>Coef</th></tr><tr><td>CC1</td><td>2</td></tr></table>')
        for expected in ("**Évaluation**", r"Note = (2\*CC1 + CC2) / 3", "- **CC1** : TP noté",
                         "[sujet](https://moodle.test/pluginfile.php/1/x.pdf)", r"\# pas un titre",
                         "| Épreuve | Coef |\n|---|---|\n| CC1 | 2 |"):
            self.assertIn(expected, md)
        for absent in ("SECRET", "```", "javascript", "<"):
            self.assertNotIn(absent, md)
        self.assertEqual(html_to_md("<p>&nbsp;</p><p></p>"), "")

    def _client(self):
        client = FakeClient()
        client.sections.append({"id": 900, "name": "Modalités de contrôle", "section": 0, "summary": self.MCC_V1,
                                "modules": [{"id": 700, "modname": "label", "name": "Consigne", "url": "",
                                             "description": "<p>Apporter son <em>ordinateur</em>.</p>"}]})
        client.add_file(101, "sujet.pdf", b"SUJET", section="TP 1")
        client.add_file(105, "index.html", b"<p>", section="TP 1", modname="page", module="Mode d'emploi")
        client.pages = [{"course": 10, "coursemodule": 105, "name": "Mode d'emploi", "intro": "", "timemodified": T0,
                         "content": '<p>Lire le <a href="https://moodle.test/webservice/pluginfile.php/5/a.pdf'
                                    '?token=SECRET">sujet</a>.</p>'}]
        client.assign_intro = "<p>Rendre un PDF.</p>"
        client.discussions = [{"name": "Salle changée", "discussion": 42, "created": T0,
                               "message": "<p>Salle B12.</p>", "userfullname": "Prof"}]
        return client

    def test_texts_cycle(self):
        from mb import texts
        tmp, cfg = make_env()
        client = self._client()
        classifier = FakeClassifier(json.dumps({"placements": []}))
        vault = tmp / "Vault/Moodle.md"

        report = sync.run(cfg, client, classifier, dry_run=True, log=quiet)
        self.assertEqual(report["texts"], 5)
        self.assertFalse(cfg.state_path.exists())
        self.assertFalse(cfg.texts_dir.exists())

        # Premier relevé : textes conservés, aucun compté comme modifié
        report = sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(report["texts_changed"], [])
        state = sync.load_state(cfg.state_path)
        stored = state["remote"]["texts"]
        self.assertEqual(set(stored), {"section:10:900", "module:700", "page:105", "assign:500", "announcement:42"})
        self.assertTrue(all(r["changed_at"] is None for r in stored.values()))
        self.assertEqual(stored["module:700"]["text"], "Apporter son *ordinateur*.")
        self.assertEqual(stored["page:105"]["section"], "TP 1")
        index.write(cfg, state)
        course_md = cfg.texts_dir / "TST.md"
        self.assertEqual(texts.write_files(cfg, state), [course_md])
        body = course_md.read_text()
        for expected in ("genere_par: moodle-bridge", "## Modalités de contrôle", "⚖️", "Rendre un PDF.",
                         "## Annonces", "Salle B12.", "Lire le [sujet](https://moodle.test/pluginfile.php/5/a.pdf)"):
            self.assertIn(expected, body)
        text = vault.read_text()
        self.assertIn("[[#Modalités de contrôle]]", text)
        mcc = text.split("\n## Modalités de contrôle\n")[1].split("\n## Cours")[0]
        self.assertIn("### TST — Modalités de contrôle", mcc)
        self.assertIn("(CC1 + 2\\*CC2) / 3", mcc)
        self.assertIn("### TST — Modalités de contrôle › Consigne", mcc)  # étiquette de la section des MCC
        self.assertIn("[Moodle](https://moodle.test/course/view.php?id=10#section-0) · relevé", mcc.split("› Consigne")[1])
        self.assertNotIn("Lire le", mcc)
        for path in (course_md, vault, cfg.state_path):
            self.assertNotIn("SECRET", path.read_text())

        # Passage sans changement : ni réécriture des textes ni appel au modèle
        prompts, mtime = len(classifier.prompts), course_md.stat().st_mtime_ns
        report = sync.run(cfg, client, classifier, log=quiet)
        state = sync.load_state(cfg.state_path)
        self.assertEqual(report["texts_changed"], [])
        self.assertEqual(texts.write_files(cfg, state), [])
        self.assertEqual(course_md.stat().st_mtime_ns, mtime)
        self.assertEqual(len(classifier.prompts), prompts)

        # Modalités modifiées sur Moodle, et l'annonce 42 sort de la fenêtre des 5 dernières
        client.sections[0]["summary"] = self.MCC_V2
        client.discussions = [{"name": f"Annonce {i}", "discussion": 50 + i, "created": T0 + 1000 * (5 - i),
                               "message": f"<p>n° {i}</p>"} for i in range(6)]
        report = sync.run(cfg, client, classifier, log=quiet)
        self.assertEqual(report["texts_changed"], ["section:10:900"])
        state = sync.load_state(cfg.state_path)
        stored = state["remote"]["texts"]
        self.assertIsNotNone(stored["section:10:900"]["changed_at"])
        self.assertIn("(CC1 + CC2) / 2", stored["section:10:900"]["text"])
        self.assertIn("announcement:42", stored)       # plus ancienne que la fenêtre : conservée
        self.assertNotIn("announcement:55", stored)    # hors fenêtre et jamais vue
        index.write(cfg, state)
        changed = vault.read_text().split("## Textes modifiés sur Moodle")[1].split("\n## ")[0]
        self.assertIn("⚖️", changed)
        self.assertIn("Modalités de contrôle", changed)
        self.assertEqual(texts.write_files(cfg, state), [course_md])

        # Commande `texts --mcc` : lue dans l'état, sans appel à Moodle
        import contextlib
        import io
        import argparse
        import moodle_bridge
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            moodle_bridge.cmd_texts(argparse.Namespace(config=str(cfg.path), course="tst", mcc=True, kind=None, json=True))
        rows = json.loads(out.getvalue())
        self.assertEqual([r["title"] for r in rows], ["Modalités de contrôle", "Consigne"])

    def test_texts_disabled(self):
        tmp, cfg = make_env(texts={"enabled": False})
        sync.run(cfg, self._client(), FakeClassifier(json.dumps({"placements": []})), log=quiet)
        state = sync.load_state(cfg.state_path)
        self.assertEqual(state["remote"]["texts"], {})
        self.assertIsNone(cfg.texts_dir)
        index.write(cfg, state)
        self.assertNotIn("## Modalités de contrôle", (tmp / "Vault/Moodle.md").read_text())


class VaultTests(unittest.TestCase):
    """Coffre Obsidian : notes reliées, régénérées sans modèle de langage."""

    def _sync(self, **extra):
        tmp, cfg = make_env(vault={"dir": "Vault/Tableau de bord"}, index={"path": None}, **extra)
        (tmp / "Vault/.obsidian").mkdir(parents=True)
        client = TextTests._client(TextTests())
        sync.run(cfg, client, FakeClassifier(json.dumps({"placements": []})), log=quiet)
        vault.write(cfg, sync.load_state(cfg.state_path))
        return tmp, cfg, client

    def test_pages_and_links(self):
        tmp, cfg, _client = self._sync()
        board = tmp / "Vault/Tableau de bord"
        names = {p.relative_to(board).as_posix() for p in board.rglob("*.md")}
        self.assertEqual(names, {"Accueil.md", "Échéances.md", "Annonces.md", "Documents.md",
                                 "Modalités de contrôle.md", "Matières/TST.md", "Devoirs/TST — Rendu projet.md"})
        self.assertFalse((tmp / "Vault/Moodle.md").exists())  # l'ancien index est désactivé
        home = (board / "Accueil.md").read_text()
        self.assertIn("genere_par: moodle-bridge", home)
        self.assertIn("[[Tableau de bord/Matières/TST\\|TST]]", home)  # lien dans un tableau : barre échappée
        self.assertIn("[[Tableau de bord/Devoirs/TST — Rendu projet\\|Rendu projet doit être rendu]]", home)
        self.assertIn("(demain)", home)
        self.assertNotIn("Notes", home.split("\n## ")[0])  # pas de page Notes sans relevé des notes

        subject = (board / "Matières/TST.md").read_text()
        for expected in ('type: "moodle-matiere"', "## Modalités de contrôle",
                         "(CC1 + 2\\*CC2) / 3", "### TP 1", "Salle changée",
                         # fichier hors du coffre : lien file://
                         (tmp / "Cours/TST/_a_classer/sujet.pdf").resolve().as_uri()):
            self.assertIn(expected, subject)
        assignment = (board / "Devoirs/TST — Rendu projet.md").read_text()
        self.assertIn("Rendre un PDF.", assignment)
        self.assertIn('matiere: "[[Tableau de bord/Matières/TST|TST]]"', assignment)
        news = (board / "Annonces.md").read_text()
        self.assertIn("Salle B12.", news)
        self.assertNotIn("Résumé (IA)", news)  # aucun résumé sans IA

    def test_files_inside_vault_use_relative_links(self):
        tmp, cfg = make_env(courses={"10": {"dest": "Vault/Cours", "code": "TST", "mode": "mirror"}},
                            vault={"dir": "Vault/Tableau de bord"})
        (tmp / "Vault/.obsidian").mkdir(parents=True)
        client = FakeClient()
        client.add_file(101, "sujet 1.pdf", b"SUJET", section="TP 1")
        sync.run(cfg, client, FakeClassifier("{}"), log=quiet)
        vault.write(cfg, sync.load_state(cfg.state_path))
        subject = (tmp / "Vault/Tableau de bord/Matières/TST.md").read_text()
        self.assertIn("(../../Cours/TP%201/sujet%201.pdf)", subject)

    def test_route_outside_course_gets_its_own_subject(self):
        tmp, cfg = make_env(courses={"10": {"dest": "Cours/TST", "code": "TST", "mode": "mirror", "routes": [
            {"sections": r"^TD\d+-", "dest": "Cours/XYZ", "code": "XYZ"},
            {"sections": r"^Bio$", "dest": "Cours/TST/Bio", "code": "TST-Bio"}]}},
            vault={"dir": "Vault"})
        client = FakeClient()
        client.add_file(401, "xyz.pdf", b"X", section="TD1- Présentation")
        client.add_file(402, "bio.pdf", b"B", section="Bio")
        sync.run(cfg, client, FakeClassifier("{}"), log=quiet)
        vault.write(cfg, sync.load_state(cfg.state_path))
        subjects = sorted(p.name for p in (tmp / "Vault/Matières").glob("*.md"))
        self.assertEqual(subjects, ["TST.md", "XYZ.md"])
        xyz = (tmp / "Vault/Matières/XYZ.md").read_text()
        self.assertIn("xyz.pdf", xyz)
        self.assertNotIn("bio.pdf", xyz)
        self.assertIn("bio.pdf", (tmp / "Vault/Matières/TST.md").read_text())

    def test_cleanup_keeps_personal_notes(self):
        tmp, cfg, client = self._sync()
        board = tmp / "Vault/Tableau de bord"
        mine = board / "Devoirs/Mes idées.md"
        mine.write_text("# À moi\n")
        stale = board / "Devoirs/TST — Ancien devoir.md"
        stale.write_text("---\ngenere_par: moodle-bridge\n---\n")
        state = sync.load_state(cfg.state_path)
        before = (board / "Matières/TST.md").stat().st_mtime_ns
        written = vault.write(cfg, state)
        self.assertNotIn(board / "Matières/TST.md", written)  # contenu inchangé : note non réécrite
        self.assertEqual((board / "Matières/TST.md").stat().st_mtime_ns, before)
        self.assertTrue(mine.exists())
        self.assertFalse(stale.exists())

    def test_grades_never_show_a_mystery(self):
        try:
            from mb import notes
        except ImportError:
            self.skipTest("édition sans relevé des notes")
        tmp, cfg, _client = self._sync(notes={"enabled": True})
        grade = lambda raw: {"raw": raw, "min": 0, "max": 20, "formatted": str(raw)}  # noqa: E731
        state = notes.empty_state()
        state["items"] = {
            "10/1": {"name": "Examen final", "courseid": 10, "status": "mystery", "grade": grade(17.25),
                     "mystery": {"emoji": "🦉", "color": "#123456", "teaser": "Une chouette surprise"},
                     "detected_at": "2026-10-30T10:00:00"},
            "10/2": {"name": "TP noté", "courseid": 10, "status": "revealed", "grade": grade(13.5),
                     "revealed_at": "2026-10-30T11:00:00"},
            "10/3": {"name": "Oral", "courseid": 10, "status": "none", "grade": None}}
        notes.state_path(cfg).write_text(json.dumps(state))
        vault.write(cfg, sync.load_state(cfg.state_path))
        board = tmp / "Vault/Tableau de bord"
        pages = "".join(p.read_text() for p in board.rglob("*.md"))
        self.assertNotIn("17,25", pages)
        self.assertNotIn("17.25", pages)
        page = (board / "Notes.md").read_text()
        for expected in ("🦉 à découvrir dans l'application", "Une chouette surprise", "13,5 / 20", "pas encore notée"):
            self.assertIn(expected, page)
        self.assertIn("1 note(s) à découvrir", (board / "Accueil.md").read_text())

        # Relevé des notes désactivé et vide : la page Notes disparaît
        notes.state_path(cfg).unlink()
        cfg.data["notes"]["enabled"] = False
        vault.write(cfg, sync.load_state(cfg.state_path))
        self.assertFalse((board / "Notes.md").exists())


@unittest.skipIf(snapshot is None, "édition sans application de barre des menus")
class SnapshotTests(unittest.TestCase):
    def _item(self, name, placed_at, status="placed", folder="Cours/TST/TD/TD03"):
        return {"courseid": 10, "course": "M1_TST", "code": "TST", "filename": name, "status": status,
                "placed_at": placed_at, "local_path": f"{folder}/{name}", "module_url": "https://moodle.test/m"}

    def test_menubar_state(self):
        tmp, cfg = make_env()
        now = int(time.time())
        state = sync.empty_state()
        state["items"] = {f"10/1/bulk{i}.pdf": self._item(f"bulk{i}.pdf", now - 10 * 86400) for i in range(30)}
        state["items"]["10/2/td3.pdf"] = self._item("td3.pdf", now - 100)
        state["items"]["10/3/cm1.pdf"] = self._item("cm1.pdf", now - 200, "zotero_pending", ".moodle-bridge/a_importer_zotero")
        state["remote"] = {"sitename": "Moodle test", "courses": [], "assignments": [], "events": [
            {"courseid": 10, "course": "M1_TST", "name": "Passé", "timestart": now - 86400, "url": "p"},
            {"courseid": 10, "course": "M1_TST", "name": "Rendu TP", "timestart": now + 3600, "url": "r"}],
            "announcements": [{"courseid": 10, "course": "M1_TST", "name": "Salle changée", "time": now - 60, "url": "a"}]}
        state["last_sync"] = "2026-10-21T15:39:44+02:00"
        state["history"] = [{"at": "2026-10-21T15:39:44+02:00", "new": 1}]
        sync.save_state(cfg.state_path, state, cfg.env_root)
        sync.record_failure(cfg, "Moodle inaccessible : HTTP 403 Forbidden", "network")

        snapshot.write(cfg, sync.load_state(cfg.state_path), running=True, scheduled=True, now=now)
        snap = snapshot.read(cfg)
        self.assertTrue(snap["running"])
        self.assertEqual(snap["pid"], os.getpid())
        self.assertEqual(snap["last_scheduled_at"], now)
        # Le lot de l'import initial ne noie pas les vraies nouveautés
        self.assertEqual([f["filename"] for f in snap["recent_files"]], ["td3.pdf", "cm1.pdf"])
        self.assertEqual(snap["recent_files"][0]["folder"], "Cours/TST/TD/TD03")
        self.assertEqual([f["filename"] for f in snap["zotero_pending"]], ["cm1.pdf"])
        self.assertEqual([u["name"] for u in snap["upcoming"]], ["Rendu TP"])
        self.assertEqual(snap["announcements"][0]["code"], "TST")
        self.assertEqual(snap["last_error_kind"], "network")
        self.assertEqual([r["ok"] for r in snap["history"]], [False, True])  # le plus récent d'abord
        self.assertEqual(snap["counts"], {"tracked": 32, "inbox": 0, "zotero_pending": 1})
        self.assertIn("token", snap["token_argv"])

        snapshot.write(cfg, sync.load_state(cfg.state_path), now=now + 60)
        snap = snapshot.read(cfg)
        self.assertFalse(snap["running"])
        self.assertIsNone(snap["pid"])
        self.assertEqual(snap["last_scheduled_at"], now)  # conservé d'un appel à l'autre

    def test_announcement_summaries(self):
        from mb import summaries
        tmp, cfg = make_env()
        now = int(time.time())
        long_md = "Le **TP noté** du jeudi aura lieu en salle 4. " * 20
        texts = {
            "announcement:1": {"kind": "announcement", "text": long_md, "sha": "a1"},
            "announcement:2": {"kind": "announcement", "text": "Cours [annulé](https://moodle.test/x) demain.", "sha": "b1"},
        }
        remote = {"announcements": [
            {"courseid": 10, "course": "M1_TST", "name": "TP noté", "time": now - 60, "url": "https://m/discuss.php?d=1"},
            {"courseid": 10, "course": "M1_TST", "name": "Annulation", "time": now - 120, "url": "https://m/discuss.php?d=2"}]}
        engine = FakeClassifier(lambda prompt: json.dumps({"summaries": [{"id": "1", "summary": "TP noté jeudi, salle 4."}]}))
        store = {"announcement:old": {"sha": "x", "text": "oubliée"}}

        summaries.update(cfg, store, remote, texts, engine, quiet, now, limit=10)
        self.assertEqual(store["announcement:1"]["text"], "TP noté jeudi, salle 4.")
        self.assertEqual(store["announcement:2"], {"sha": "b1", "text": "Cours annulé demain.", "by": "texte", "at": now})
        self.assertNotIn("announcement:old", store)                # suit les textes conservés
        self.assertEqual(len(engine.prompts), 1)
        self.assertNotIn("Annulation", engine.prompts[0])          # une annonce courte se passe du modèle

        summaries.update(cfg, store, remote, texts, engine, quiet, now + 60, limit=10)
        self.assertEqual(len(engine.prompts), 1)                    # rien de nouveau : aucun appel
        texts["announcement:1"] = {**texts["announcement:1"], "sha": "a2"}
        summaries.update(cfg, store, remote, texts, engine, quiet, now + 120, limit=10)
        self.assertEqual(len(engine.prompts), 2)                    # texte modifié : nouveau résumé

        # Modèle en échec : extrait du texte en attendant, et pas plus de MAX_ATTEMPTS essais
        texts["announcement:1"] = {**texts["announcement:1"], "sha": "a3"}
        broken = FakeClassifier("pas du JSON")
        for _ in range(summaries.MAX_ATTEMPTS + 2):
            summaries.update(cfg, store, remote, texts, broken, quiet, now, limit=10)
        self.assertEqual(len(broken.prompts), summaries.MAX_ATTEMPTS)
        summary, kind = summaries.for_snapshot(store, texts, remote["announcements"][0])
        self.assertEqual(kind, "extrait")
        self.assertTrue(summary.startswith("Le TP noté du jeudi") and summary.endswith("…"))
        self.assertLessEqual(len(summary), summaries.MAX_CHARS + 1)

        state = sync.empty_state()
        state["remote"] = {**remote, "texts": texts, "courses": []}
        state["summaries"] = store
        snap = snapshot.build(cfg, state, now)
        self.assertEqual([(a["summary"], a["summary_kind"]) for a in snap["announcements"]][1],
                         ("Cours annulé demain.", "texte"))

    def test_http_error_message_keeps_page_title(self):
        import io
        import urllib.error
        import moodle_bridge
        page = "<html><head><title>Accès interdit - Erreur 403</title></head></html>".encode()
        exc = urllib.error.HTTPError("https://moodle.test", 403, "Forbidden", {}, io.BytesIO(page))
        self.assertEqual(moodle_bridge.describe_http_error(exc),
                         "Moodle inaccessible : HTTP 403 Forbidden — « Accès interdit - Erreur 403 »")


class ApiTests(unittest.TestCase):
    def test_flatten(self):
        self.assertEqual(api.flatten({"courseids": [1, 2], "x": None, "o": {"a": 1}}),
                         [("courseids[0]", "1"), ("courseids[1]", "2"), ("o[a]", "1")])


if __name__ == "__main__":
    unittest.main()
