import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import safety


class SafetyTests(unittest.TestCase):
    def test_nested_redaction(self):
        d = {
            "items": [{"api_key": "secret", "url": "postgres://person:secret@db/x"}],
            "password": "hidden",
        }
        text = json.dumps(safety.scrub(d))
        self.assertNotIn("secret", text)
        self.assertNotIn("person:", text)
        self.assertNotIn("hidden", text)

    def test_no_ambient_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(safety.SafeError):
                safety.secret("TEST_KEY", "TEST_KEY_FILE")

    def test_explicit_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "key"
            p.write_text("fixture-credential")
            p.chmod(0o600)
            with patch.dict(os.environ, {"TEST_KEY_FILE": str(p)}, clear=True):
                self.assertEqual(
                    safety.secret("TEST_KEY", "TEST_KEY_FILE"), "fixture-credential"
                )
                self.assertEqual(
                    safety.scrub("echo fixture-credential"), "echo [REDACTED]"
                )
                if os.name != "nt":
                    p.chmod(0o644)
                    with self.assertRaises(safety.SafeError):
                        safety.secret("TEST_KEY", "TEST_KEY_FILE")

    def test_url_boundary(self):
        for path in [
            "//evil.test/a",
            "https://evil.test",
            "/../a",
            "/%2e%2e/a",
            "/x#y",
            "/x\\y",
        ]:
            with self.assertRaises(safety.SafeError):
                safety.api_url("https://provider.test", path)
        self.assertEqual(
            safety.api_url("https://provider.test", "/x?a=1", {"b": 2}),
            "https://provider.test/x?a=1&b=2",
        )

    def test_error_body_suppressed(self):
        from urllib.error import HTTPError

        e = HTTPError(
            "https://provider.test", 401, "secret", {}, io.BytesIO(b"private response")
        )
        with patch("safety.build_opener") as op:
            op.return_value.open.side_effect = e
            with self.assertRaises(safety.SafeError) as caught:
                safety.request("https://provider.test")
            self.assertNotIn("private", str(caught.exception))

    def test_redirect_refused(self):
        self.assertIsNone(
            safety.NoRedirect().redirect_request(
                None, None, 302, None, None, "https://evil.test"
            )
        )

    def test_private_export_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.json"
            safety.private_json(p, {"secret": "hidden"})
            self.assertNotIn("hidden", p.read_text())
            if os.name != "nt":
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                safety.private_json(p, {})
