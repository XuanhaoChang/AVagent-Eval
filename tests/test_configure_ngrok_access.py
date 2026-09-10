import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("configure_ngrok_access", Path(__file__).resolve().parents[1] / "scripts/configure_ngrok_access.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NgrokConfigurationTests(unittest.TestCase):
    def test_endpoint_validation(self):
        self.assertEqual(MODULE.endpoint_url(" example.ngrok-free.dev\r\n"), "https://example.ngrok-free.dev")
        for value in ("http://example.ngrok.app", "https://a:b@example.ngrok.app", "example.ngrok.app/path",
                      "example.ngrok.app?token=private", "example.ngrok.app:443", "not-ngrok.example",
                      "example.ngrok.app\nmalicious", "https://"):
            with self.assertRaises(ValueError, msg=value):
                MODULE.endpoint_url(value)

    def test_secret_config_and_public_endpoint_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = MODULE.configure(Path(directory), "example.ngrok-free.app", "fixture-not-a-real-token-123")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertIn("web_addr: false", path.read_text())
            self.assertIn("http://127.0.0.1:8766", path.read_text())
            self.assertEqual((path.parent / "endpoint.txt").read_text(), "https://example.ngrok-free.app\n")

    def test_token_injection_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            for token in ("short", "token-with-newline\nagent: evil", "ngrok config add-authtoken token"):
                with self.assertRaises(ValueError):
                    MODULE.configure(Path(directory), "example.ngrok.app", token)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
