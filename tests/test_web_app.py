"""Static contracts of the live evaluation console (no browser dependencies)."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit


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
            self.assertTrue((ROOT / "web_app" / urlsplit(path).path).is_file(), path)

    def test_uses_real_jobs_without_browser_credentials(self):
        self.assertIn('api("/api/jobs", { method: "POST", body: form })', self.script)
        self.assertIn("not_evaluable", self.script)
        self.assertNotIn("localStorage", self.script)
        self.assertNotIn("sessionStorage", self.script)
        self.assertNotIn("innerHTML", self.script)
        self.assertNotIn('id="access-token"', self.page)
        self.assertNotIn("Authorization:", self.script)
        self.assertIn('if (defaultBase) connectBackend();', self.script)
        self.assertIn('publicAccess: true', self.script)
        for removed in ("connection-panel", "connection-summary", "connection-form", "api-base"):
            self.assertNotIn(f'id="{removed}"', self.page)
            self.assertNotIn(f'$("{removed}")', self.script)
        self.assertNotIn("服务器状态与高级设置", self.page)
        self.assertNotIn("ngrok-free.dev", self.page)
        self.assertIn('id="retry-connection"', self.page)

    def test_personal_repository_and_explicit_public_configuration(self):
        self.assertIn("https://github.com/XuanhaoChang/AVagent-Eval", self.page)
        config = (ROOT / "web_app/config.js").read_text()
        base = re.search(r'apiBase:\s*"([^"]*)"', config).group(1)
        self.assertTrue(not base or base.startswith("https://"))
        if ".trycloudflare.com" in base:
            self.assertIn('deploymentMode: "temporary"', config)
        if ".ngrok-free." in base:
            self.assertIn('deploymentMode: "fixed"', config)
        self.assertNotIn("DEMO RUN", self.page)

    def test_page_is_for_evaluation_not_shared_administration(self):
        for removed in ("history-title", "history-list", "refresh-history", "check-list",
                        "coverage-note", "metric-coverage", "model-info", "job-id"):
            self.assertNotIn(removed, self.parser.ids)
            self.assertNotIn(f'$("{removed}")', self.script)
        for text in ("不会将工具失败", "无法评估", "运行记录", "检查覆盖", "证据边界",
                     "非人工标注", "证据不足时保留未知", "运行配置与报告说明"):
            self.assertNotIn(text, self.page + self.script)
        self.assertNotIn("refreshHistory", self.script)
        self.assertNotIn('api("/api/jobs", {},', self.script)
        self.assertIn('id="report-notice"', self.page)
        self.assertIn('id="result-metrics"', self.page)

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

    def test_ngrok_api_requests_skip_html_interstitial_without_dropping_auth(self):
        self.assertIn('"ngrok-skip-browser-warning": "1"', self.script)
        self.assertIn('...(ngrok ?', self.script)
        self.assertNotIn('Authorization:', self.script)
        self.assertIn('credentials: "omit"', self.script)


if __name__ == "__main__":
    unittest.main()
