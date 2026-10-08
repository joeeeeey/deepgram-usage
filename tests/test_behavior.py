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
import deepgram_ops as cli
from argparse import Namespace


class Behavior(unittest.TestCase):
    def test_bounded_range(self):
        for start, end in [("2026-01-02", "2026-01-01"), ("2026-01-01", "2026-03-01")]:
            with self.assertRaises(ValueError):
                cli.validate_dates(Namespace(start=start, end=end, param=[]))

    def test_cannot_override_date_bounds(self):
        with self.assertRaises(ValueError):
            cli.validate_dates(
                Namespace(
                    start="2026-01-01", end="2026-01-02", param=["start=2000-01-01"]
                )
            )

    def test_usage_sum(self):
        self.assertEqual(cli.sum_numeric([{"hours": 2}, {"hours": 3}], ("hours",)), 5)

    def test_get_uses_management_token(self):
        with (
            patch.dict(os.environ, {"DEEPGRAM_API_KEY": "synthetic"}, clear=True),
            patch("deepgram_ops.request", return_value=(200, {"projects": []})) as req,
        ):
            cli.api_get("/projects")
            self.assertEqual(
                req.call_args.kwargs["headers"]["Authorization"], "Token synthetic"
            )

    def test_numeric_usage_survives_transport_redaction(self):
        from safety import scrub

        value = scrub(
            {"results": [{"tokens_in": 120, "tokens_out": 80, "api_token": "hidden"}]}
        )
        self.assertEqual(cli.sum_numeric(cli.iter_records(value), ("tokens_in",)), 120)
        self.assertEqual(cli.sum_numeric(cli.iter_records(value), ("tokens_out",)), 80)
        self.assertNotIn("hidden", json.dumps(value))

    def test_whitespace_cannot_override_date_bounds(self):
        with self.assertRaises(ValueError):
            cli.validate_dates(
                Namespace(
                    start="2026-01-01", end="2026-01-02", param=["start =2000-01-01"]
                )
            )
