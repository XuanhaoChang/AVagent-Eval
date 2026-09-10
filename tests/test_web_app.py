"""Static contracts of the live evaluation console (no browser dependencies)."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.assets = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "script" and attributes.get("src"):
            self.assets.append(attributes["src"])
        if tag == "link" and attributes.get("href"):
            self.assets.append(attributes["href"])


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.page = (ROOT / "web_app/index.html").read_text()
        self.script = (ROOT / "web_app/app.js").read_text()
        self.parser = PageParser()
        self.parser.feed(self.page)

    def test_all_script_ids_exist_and_are_unique(self):
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        referenced = set(re.findall(r'\$\("([^"\n]+)"\)', self.script))
        self.assertFalse(referenced - set(self.parser.ids))

    def test_assets_resolve_below_github_project_path(self):
        for path in self.parser.assets:
            self.assertFalse(path.startswith(("/", "http:")))
            self.assertTrue((ROOT / "web_app" / path).is_file(), path)

    def test_uses_real_jobs_and_no_persistent_browser_tokens(self):
        self.assertIn('api("/api/jobs", { method: "POST", body: form })', self.script)
        self.assertIn("not_evaluable", self.script)
        self.assertNotIn("localStorage", self.script)
        self.assertNotIn("sessionStorage", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_personal_repository_and_explicit_public_configuration(self):
        self.assertIn("https://github.com/XuanhaoChang/AVagent-Eval", self.page)
        config = (ROOT / "web_app/config.js").read_text()
        base = re.search(r'apiBase:\s*"([^"]*)"', config).group(1)
        self.assertTrue(not base or base.startswith("https://"))
        if ".trycloudflare.com" in base:
            self.assertIn('deploymentMode: "temporary"', config)
        self.assertNotIn("DEMO RUN", self.page)
        self.assertIn("非准确率", self.script)

    def test_events_replace_polling_and_keep_manual_recovery(self):
        events = (ROOT / "web_app/job-events.js").read_text()
        self.assertIn("new window.AvagentJobEvents", self.script)
        self.assertNotIn("beginPolling", self.script)
        self.assertNotIn("state.poll", self.script)
        self.assertIn('id="refresh-job"', self.page)
        self.assertIn('type: "authenticate", token: this.token', events)
        self.assertNotIn("fetch(", events)
        self.assertNotIn("?token", events)
        self.assertIn("this.retries >= delays.length", events)


if __name__ == "__main__":
    unittest.main()
