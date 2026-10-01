import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bridge import codex_watch_bridge as bridge


class LocalActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.history = sqlite3.connect(self.home / "thread_history_1.sqlite")
        self.state = sqlite3.connect(self.home / "state_5.sqlite")
        self.history.execute("""
            CREATE TABLE thread_turns (
                thread_id TEXT, rollout_ordinal INTEGER, status TEXT
            )
        """)
        self.state.execute("""
            CREATE TABLE threads (
                id TEXT, name TEXT, title TEXT, preview TEXT,
                updated_at INTEGER, archived INTEGER
            )
        """)

    def tearDown(self):
        self.history.close()
        self.state.close()
        self.temp.cleanup()

    def add_thread(self, thread_id, turns, updated_at=1000, archived=0):
        self.state.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, f"Task {thread_id}", "", "", updated_at, archived),
        )
        self.history.executemany(
            "INSERT INTO thread_turns VALUES (?, ?, ?)",
            [(thread_id, index, status) for index, status in enumerate(turns)],
        )
        self.state.commit()
        self.history.commit()

    def test_only_fresh_latest_unarchived_turn_is_active(self):
        self.add_thread("running", ["completed", "inProgress"])
        self.add_thread("finished", ["inProgress", "completed"])
        self.add_thread("archived", ["inProgress"], archived=1)
        self.add_thread("stale", ["inProgress"], updated_at=-7000)

        active = bridge._local_active_threads(self.home, now=1000)

        self.assertEqual([item["id"] for item in active], ["running"])

    def test_normalize_uses_desktop_state_when_app_server_says_not_loaded(self):
        self.add_thread("running", ["inProgress"])
        app_server = {"data": [{"id": "running", "status": {"type": "notLoaded"}}]}
        original_lookup = bridge._local_active_threads
        try:
            bridge._local_active_threads = lambda: original_lookup(self.home, now=1000)
            activity = bridge._normalize({}, app_server)["activity"]
        finally:
            bridge._local_active_threads = original_lookup

        self.assertTrue(activity["running"])
        self.assertEqual(activity["count"], 1)
        self.assertEqual(activity["items"][0]["title"], "Task running")

    def test_unavailable_quota_is_not_reported_as_full(self):
        original_usage = bridge._local_usage
        bridge._local_usage = lambda: {
            "todayTokens": 0, "inputTokens": 0, "outputTokens": 0,
            "cachedTokens": 0, "bars": [0] * 12, "peakLabel": "--:--",
            "currentModel": "", "_rateLimits": None,
        }
        try:
            snapshot = bridge._normalize({}, {"data": []})
        finally:
            bridge._local_usage = original_usage

        self.assertFalse(snapshot["quotaAvailable"])
        self.assertIsNone(snapshot["quota"]["fiveHour"]["remainingPercent"])

    def test_missing_desktop_database_is_unknown_not_idle(self):
        missing_home = self.home / "missing"
        self.assertIsNone(bridge._local_active_threads(missing_home, now=1000))

        original_lookup = bridge._local_active_threads
        try:
            bridge._local_active_threads = lambda: None
            activity = bridge._normalize({}, {"data": []})["activity"]
        finally:
            bridge._local_active_threads = original_lookup
        self.assertFalse(activity["available"])
        self.assertFalse(activity["running"])

    def test_invalid_quota_number_stays_unavailable(self):
        window = bridge._window({"usedPercent": "not-a-number", "resetsAt": 123})

        self.assertIsNone(window["usedPercent"])
        self.assertIsNone(window["remainingPercent"])
        self.assertEqual(window["resetsAt"], 123)

    def test_malformed_token_values_do_not_break_snapshot(self):
        values = bridge._token_values({
            "input_tokens": "bad",
            "cached_input_tokens": None,
            "output_tokens": 12,
            "total_tokens": "",
        })

        self.assertEqual(values, {"input": 0, "cached": 0, "output": 12, "total": 0})

    def test_normalize_uses_fresh_local_quota_when_rpc_is_unavailable(self):
        original_usage = bridge._local_usage
        original_activity = bridge._local_active_threads
        bridge._local_usage = lambda: {
            "todayTokens": 0, "inputTokens": 0, "outputTokens": 0,
            "cachedTokens": 0, "bars": [0] * 12, "peakLabel": "--:--",
            "currentModel": "", "_rateLimits": {
                "primary": {"used_percent": 24, "window_minutes": 300, "resets_at": 4102444800},
                "secondary": {"used_percent": 56, "window_minutes": 10080, "resets_at": 4102444800},
                "plan_type": "plus",
            },
        }
        bridge._local_active_threads = lambda: []
        try:
            snapshot = bridge._normalize({}, {"data": []})
        finally:
            bridge._local_usage = original_usage
            bridge._local_active_threads = original_activity

        self.assertTrue(snapshot["quotaAvailable"])
        self.assertEqual(snapshot["quota"]["fiveHour"]["remainingPercent"], 76)
        self.assertEqual(snapshot["quota"]["weekly"]["remainingPercent"], 44)
        self.assertEqual(snapshot["planType"], "plus")

    def test_long_lived_session_contributes_to_today_usage(self):
        session_dir = self.home / "sessions" / "2026" / "09" / "23"
        session_dir.mkdir(parents=True)
        event = {
            "timestamp": "2026-10-02T10:00:00+00:00",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 10,
                        "total_tokens": 110,
                    }
                },
                "rate_limits": {
                    "primary": {"used_percent": 25, "resets_at": 4102444800},
                    "secondary": {"used_percent": 50, "resets_at": 4102444800},
                },
            },
        }
        (session_dir / "long-running.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

        usage = bridge._scan_local_usage(
            self.home,
            now=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(usage["todayTokens"], 110)
        self.assertEqual(usage["cachedTokens"], 20)
        self.assertEqual(usage["_rateLimits"]["primary"]["used_percent"], 25)


if __name__ == "__main__":
    unittest.main()
