import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from nora_publish import build_export, publish, reconcile

FORBIDDEN = (
    "session_id", "chat_id", "thread_id", "title", "content", "message",
    "system_prompt", "credential", "password", "token_count", "user_id",
)


def make_fixture(tmpdir, nora="nora.db", ledger="ledger.db"):
    source = os.path.join(tmpdir, nora)
    ledge = os.path.join(tmpdir, ledger)
    with closing(sqlite3.connect(source)) as db, db:
        db.executescript(
            """
            CREATE TABLE sessions(
                id TEXT, source TEXT, chat_type TEXT, ended_at REAL,
                last_activity_at REAL, started_at REAL,
                actual_cost_usd REAL, estimated_cost_usd REAL, cost_status TEXT);
            CREATE TABLE session_model_usage(
                session_id TEXT, model TEXT, task TEXT, billing_provider TEXT,
                billing_base_url TEXT, billing_mode TEXT, input_tokens INTEGER,
                output_tokens INTEGER, cache_read_tokens INTEGER,
                estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT,
                first_seen REAL, last_seen REAL);
            """
        )
    return source, ledge


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source, self.ledger = make_fixture(self.tmp.name)
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()

    def insert_usage(self, sid="live", model="gpt-6-sol", task="", ins=100, outs=20,
                     cache=50, estimated=0.3, actual=0.0, status="estimated", seen=None):
        seen = seen or self.now
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, model, task, "openrouter", None, None, ins, outs, cache,
                 estimated, actual, status, seen, seen),
            )

    def insert_session(self, sid="live", seen=None, ended=None, source="telegram",
                       estimated=0.3, actual=0.0, status="estimated"):
        seen = seen or self.now
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, source, "dm", ended, seen, seen - 1800, actual, estimated, status),
            )

    def test_export_shape_has_only_aggregate_keys(self):
        self.insert_session()
        self.insert_usage()
        export = build_export(self.source, self.ledger, self.now)
        self.assertEqual(export["agent"], "Nora")
        self.assertEqual(export["refresh_seconds"], 90)
        self.assertEqual(set(export), {"agent", "published_at", "refresh_seconds",
                                       "partial", "today", "session", "last_24h",
                                       "reconciliation", "media_gen"})
        self.assertEqual(set(export["session"]),
                         {"status", "model", "input_tokens", "output_tokens",
                          "cache_read_tokens", "cost_usd"})
        self.assertEqual(set(export["reconciliation"]),
                         {"status", "total_cost_usd", "session_summary_cost_usd",
                          "auxiliary_cost_usd", "difference_usd", "checked_at"})

    def test_export_json_leaks_no_private_metadata(self):
        self.insert_session()
        self.insert_usage()
        export = build_export(self.source, self.ledger, self.now)
        payload = json.dumps(export, separators=(",", ":"), ensure_ascii=False)
        for word in FORBIDDEN:
            self.assertNotIn(word, payload)
        self.assertNotIn(self.source, payload)
        self.assertNotIn(self.ledger, payload)

    def test_reconcile_ok_when_costs_match(self):
        rows = [
            {"session_id": "a", "model": "m", "task": "", "cost_status": "estimated",
             "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5},
        ]
        sessions = [
            {"id": "a", "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5,
             "cost_status": "estimated"},
        ]
        out = reconcile(rows, sessions, "t")
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["difference_usd"], 0.0)

    def test_reconcile_flags_mismatch_on_drift(self):
        rows = [
            {"session_id": "a", "model": "m", "task": "", "cost_status": "estimated",
             "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5},
        ]
        sessions = [
            {"id": "a", "actual_cost_usd": 0.0, "estimated_cost_usd": 1.5,
             "cost_status": "estimated"},
        ]
        out = reconcile(rows, sessions, "t")
        self.assertEqual(out["status"], "mismatch")
        self.assertAlmostEqual(out["difference_usd"], -1.0)

    def test_reconcile_unavailable_with_no_usage_rows(self):
        sessions = [{"id": "a", "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5,
                     "cost_status": "estimated"}]
        self.assertEqual(reconcile([], sessions, "t")["status"], "unavailable")

    def test_reconcile_treats_included_as_zero_on_both_sides(self):
        rows = [
            {"session_id": "a", "model": "m", "task": "", "cost_status": "included",
             "actual_cost_usd": 0.0, "estimated_cost_usd": 0.99},
        ]
        sessions = [{"id": "a", "actual_cost_usd": 0.0, "estimated_cost_usd": 0.99,
                     "cost_status": "included"}]
        out = reconcile(rows, sessions, "t")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["session_summary_cost_usd"], 0.0)
        self.assertEqual(out["total_cost_usd"], 0.0)

    def test_reconcile_auxiliary_rows_do_not_force_mismatch(self):
        # A settled conversation row plus a NULL-status auxiliary task row. The
        # aux row is tracked and folded into total_cost_usd, but it must NOT
        # drive the ok/mismatch status (the old invariant did, permanently).
        rows = [
            {"session_id": "a", "model": "m", "task": "", "cost_status": "estimated",
             "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5},
            {"session_id": "a", "model": "m", "task": "background_review",
             "cost_status": None, "actual_cost_usd": 0.0, "estimated_cost_usd": 0.2},
        ]
        sessions = [{"id": "a", "actual_cost_usd": 0.0, "estimated_cost_usd": 0.5,
                     "cost_status": "estimated"}]
        out = reconcile(rows, sessions, "t")
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["session_summary_cost_usd"], 0.5)
        self.assertAlmostEqual(out["auxiliary_cost_usd"], 0.2)
        self.assertAlmostEqual(out["total_cost_usd"], 0.7)
        self.assertAlmostEqual(out["difference_usd"], 0.0)

    def test_session_cost_folds_in_auxiliary_rows(self):
        # The widget's "this session" figure is consolidated: conversation plus
        # the session's own auxiliary task rows, not conversation alone.
        self.insert_session(sid="live", estimated=0.3)
        self.insert_usage(sid="live", model="gpt-6-sol", task="", estimated=0.3,
                          status="estimated", ins=100, outs=20)
        self.insert_usage(sid="live", model="gpt-6-sol", task="title_generation",
                          estimated=0.1, status=None, ins=10, outs=2)
        export = build_export(self.source, self.ledger, self.now)
        self.assertAlmostEqual(export["session"]["cost_usd"], 0.4)
        self.assertEqual(export["session"]["input_tokens"], 110)

    def test_media_gen_is_key_scoped_not_account_scoped(self):
        # The media figure must be attributed to Nora's own key, not read
        # account-wide. The export carries key_scoped=True and no account_scoped.
        self.insert_session()
        self.insert_usage()
        export = build_export(self.source, self.ledger, self.now)
        mg = export["media_gen"]
        self.assertTrue(mg["key_scoped"])
        self.assertNotIn("account_scoped", mg)

    def test_publish_writes_atomically_readable_json(self):
        self.insert_session()
        self.insert_usage()
        export = build_export(self.source, self.ledger, self.now)
        out = os.path.join(self.tmp.name, "sub", "nora-usage.json")
        path = publish(export, out)
        self.assertEqual(path, out)
        with open(out, encoding="utf-8") as fh:
            parsed = json.load(fh)
        self.assertEqual(parsed["agent"], "Nora")
        leftovers = [f for f in os.listdir(os.path.dirname(out)) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_publish_is_idempotent_across_same_timestamp(self):
        self.insert_session()
        self.insert_usage()
        build_export(self.source, self.ledger, self.now)
        first = build_export(self.source, self.ledger, self.now)
        second = build_export(self.source, self.ledger, self.now)
        self.assertEqual(first["today"]["cost_usd"], second["today"]["cost_usd"])

    def test_cli_emits_export_and_writes_file(self):
        self.insert_session()
        self.insert_usage()
        out = os.path.join(self.tmp.name, "nora-usage.json")
        subprocess.run(
            [sys.executable, "nora_publish.py", "--nora-db", self.source,
             "--ledger", self.ledger, "--out", out],
            cwd=os.path.dirname(__file__), capture_output=True, text=True, check=True,
        )
        with open(out, encoding="utf-8") as fh:
            parsed = json.load(fh)
        self.assertEqual(parsed["session"]["input_tokens"], 100)
        for word in FORBIDDEN:
            self.assertNotIn(word, json.dumps(parsed))


if __name__ == "__main__":
    unittest.main()