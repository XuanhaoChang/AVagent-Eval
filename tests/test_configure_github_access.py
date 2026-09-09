"""Credential setup tests: no network access or actual credential changes."""

import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/configure_github_access.py"
spec = importlib.util.spec_from_file_location("configure_github_access", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ConfigureGitHubAccessTests(unittest.TestCase):
    def test_personal_repository_is_default(self):
        self.assertEqual(setup.DEFAULT_REPOSITORY, "XuanhaoChang/AVagent-Eval")

    def test_paste_line_endings_are_removed(self):
        result = setup.credential_input(setup.DEFAULT_REPOSITORY, "XuanhaoChang", "test-only-token\r\n")
        self.assertNotIn("\r", result)
        self.assertIn("path=XuanhaoChang/AVagent-Eval.git\n", result)
        self.assertTrue(result.endswith("password=test-only-token\n\n"))

    def test_control_character_injection_is_rejected(self):
        for token in ("", "abc\rdef", "abc\ndef", "abc\x00def", "abc def"):
            with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                setup.credential_input(setup.DEFAULT_REPOSITORY, "XuanhaoChang", token)
        with self.assertRaises(ValueError):
            setup.credential_input(setup.DEFAULT_REPOSITORY, "name\r", "test-only-token")
        with self.assertRaises(ValueError):
            setup.credential_input("owner/repo\npassword=oops", "name", "test-only-token")

    @patch.object(setup.subprocess, "run")
    def test_credential_only_passed_on_stdin_and_traces_removed(self, run):
        run.return_value = subprocess.CompletedProcess([], 0)
        with patch.dict(os.environ, {"GIT_TRACE": "1", "GIT_TRACE_CURL": "1", "GIT_CURL_VERBOSE": "1"}):
            setup.cache_credential(setup.DEFAULT_REPOSITORY, "XuanhaoChang", "test-only-token")
        args, kwargs = run.call_args
        self.assertNotIn("test-only-token", " ".join(args[0]))
        self.assertIn("password=test-only-token", kwargs["input"])
        self.assertFalse(any(key.startswith("GIT_TRACE") for key in kwargs["env"]))
        self.assertNotIn("GIT_CURL_VERBOSE", kwargs["env"])
        self.assertIn("credential.useHttpPath=true", args[0])
        self.assertIn("credential.helper=cache --timeout=3600", args[0])

    @patch.object(setup.subprocess, "run")
    def test_helper_error_cannot_echo_token(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, stderr="test-only-token")
        with self.assertRaises(RuntimeError) as error:
            setup.cache_credential(setup.DEFAULT_REPOSITORY, "XuanhaoChang", "test-only-token")
        self.assertNotIn("test-only-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
