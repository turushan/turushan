import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.contribution_record import (
    END, START, RecordError, calendar_max, graphql, ranges_for, run, updated_files,
)


README = f"Before\n{START}\n🔥 **Single-day contribution record: 689**\n{END}\nAfter\n"
STATE = {"record": 689, "history_scanned": True}


def calendar(start, end, count=1):
    return {"weeks": [{"contributionDays": [
        {"date": (start + dt.timedelta(days=i)).isoformat(), "contributionCount": count}
        for i in range((end - start).days + 1)
    ]}]}


class RecordTests(unittest.TestCase):
    def test_only_a_strictly_higher_count_changes_the_record(self):
        for count in (0, 688, 689):
            self.assertEqual(updated_files(README, STATE, count, False), (README, STATE))
        text, state = updated_files(README, STATE, 1200, False)
        self.assertIn("record: 1,200", text)
        self.assertTrue(text.startswith("Before\n"))
        self.assertTrue(text.endswith("\nAfter\n"))
        self.assertEqual(state, {"record": 1200, "history_scanned": True})

    def test_initial_scan_persists_even_without_a_higher_count(self):
        text, state = updated_files(README, {**STATE, "history_scanned": False}, 50, True)
        self.assertEqual(text, README)
        self.assertTrue(state["history_scanned"])

    def test_missing_duplicate_and_reversed_markers_fail(self):
        for text in ("none", README + START, README + END, END + START):
            with self.assertRaises(RecordError):
                updated_files(text, STATE, 700, False)

    def test_invalid_state_and_counts_fail(self):
        for state, count in (({**STATE, "record": -1}, 1), ({**STATE, "history_scanned": "yes"}, 1), (STATE, True), (STATE, -1)):
            with self.assertRaises(RecordError):
                updated_files(README, state, count, False)

    def test_daily_window_crosses_year_boundary_and_excludes_today(self):
        self.assertEqual(ranges_for(dt.date(2027, 1, 2), [2026, 2027], False), [
            (dt.date(2026, 12, 26), dt.date(2027, 1, 1)),
        ])

    def test_history_windows_include_leap_day_and_exclude_future(self):
        self.assertEqual(ranges_for(dt.date(2025, 1, 1), [2025, 2024, 2023], True), [
            (dt.date(2023, 1, 1), dt.date(2023, 12, 31)),
            (dt.date(2024, 1, 1), dt.date(2024, 12, 31)),
        ])

    def test_partial_duplicate_or_negative_calendar_fails(self):
        start, end = dt.date(2024, 2, 28), dt.date(2024, 3, 1)
        self.assertEqual(calendar_max(calendar(start, end, 12), start, end), 12)
        for kind in ("missing", "duplicate", "negative"):
            data = calendar(start, end)
            days = data["weeks"][0]["contributionDays"]
            if kind == "missing":
                days.pop()
            elif kind == "duplicate":
                days.append(days[0])
            else:
                days[0]["contributionCount"] = -1
            with self.assertRaises(RecordError):
                calendar_max(data, start, end)

    def test_today_is_not_a_record_candidate(self):
        yesterday = dt.date(2026, 9, 4)
        data = calendar(yesterday, yesterday, 2)
        data["weeks"][0]["contributionDays"].append({"date": "2026-09-05", "contributionCount": 99999})
        self.assertEqual(calendar_max(data, yesterday, yesterday), 2)

    def test_public_only_token_is_rejected(self):
        response = io.BytesIO(b'{"data": {}}')
        response.headers = {"X-OAuth-Scopes": "repo, workflow"}
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(RecordError, "read:user"):
                graphql("synthetic-token", "query {}")

    def test_graphql_error_is_not_logged_verbatim(self):
        response = io.BytesIO(b'{"errors": [{"message": "private details"}]}')
        response.headers = {"X-OAuth-Scopes": "read:user"}
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(RecordError) as error:
                graphql("synthetic-token", "query {}")
            self.assertNotIn("private details", str(error.exception))

    def fixture(self, root, scanned=True):
        (root / ".github").mkdir()
        (root / "README.md").write_text(README)
        (root / ".github/contribution-record.json").write_text(json.dumps({**STATE, "history_scanned": scanned}))

    def api(self, calls, count=1, login="turushan", fail=False):
        def fetch(token, query, variables=None):
            calls.append(variables)
            if variables is None:
                return {"viewer": {"login": login, "contributionsCollection": {"contributionYears": [2026, 2025]}}}
            if fail:
                raise RecordError("Synthetic failure")
            start, end = (dt.date.fromisoformat(variables[key][:10]) for key in ("from", "to"))
            return {"viewer": {"contributionsCollection": {"contributionCalendar": calendar(start, end, count)}}}
        return fetch

    @patch.dict("os.environ", {"CONTRIBUTIONS_TOKEN": "synthetic-token", "PROFILE_OWNER": "turushan", "GITHUB_EVENT_NAME": "schedule"})
    def test_first_run_scans_history_and_publishes_only_number(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root, scanned=False)
            calls, output = [], io.StringIO()
            with contextlib.redirect_stdout(output):
                run(root, dt.date(2026, 9, 5), self.api(calls, count=700))
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[-1]["to"], "2026-09-04T23:59:59Z")
            text = (root / "README.md").read_text()
            state = json.loads((root / ".github/contribution-record.json").read_text())
            self.assertIn("record: 700", text)
            self.assertEqual(state, {"record": 700, "history_scanned": True})
            self.assertNotIn("2026", text + json.dumps(state) + output.getvalue())

    @patch.dict("os.environ", {"CONTRIBUTIONS_TOKEN": "synthetic-token", "PROFILE_OWNER": "turushan", "GITHUB_EVENT_NAME": "schedule"})
    def test_normal_day_checks_seven_days_without_touching_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            before = {p: p.stat().st_mtime_ns for p in (root / "README.md", root / ".github/contribution-record.json")}
            calls = []
            with contextlib.redirect_stdout(io.StringIO()):
                run(root, dt.date(2026, 9, 5), self.api(calls))
            self.assertEqual(calls[1], {"from": "2026-08-29T00:00:00Z", "to": "2026-09-04T23:59:59Z"})
            self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})

    @patch.dict("os.environ", {"CONTRIBUTIONS_TOKEN": "synthetic-token", "PROFILE_OWNER": "turushan", "GITHUB_EVENT_NAME": "schedule"})
    def test_sunday_and_manual_runs_scan_full_history(self):
        for day, event in ((6, "schedule"), (5, "workflow_dispatch")):
            with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"GITHUB_EVENT_NAME": event}):
                root = Path(folder)
                self.fixture(root)
                calls = []
                with contextlib.redirect_stdout(io.StringIO()):
                    run(root, dt.date(2026, 9, day), self.api(calls))
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1]["from"], "2025-01-01T00:00:00Z")

    @patch.dict("os.environ", {"CONTRIBUTIONS_TOKEN": "synthetic-token", "PROFILE_OWNER": "turushan", "GITHUB_EVENT_NAME": "schedule"})
    def test_wrong_account_and_api_failure_preserve_files(self):
        for fetch in (self.api([], login="someone-else"), self.api([], fail=True)):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.fixture(root)
                before = {p: p.read_bytes() for p in (root / "README.md", root / ".github/contribution-record.json")}
                with self.assertRaises(RecordError):
                    run(root, dt.date(2026, 9, 5), fetch)
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    @patch.dict("os.environ", {"CONTRIBUTIONS_TOKEN": ""})
    def test_missing_secret_fails_with_setup_instruction(self):
        with self.assertRaisesRegex(RecordError, "Add the CONTRIBUTIONS_TOKEN"):
            run()


if __name__ == "__main__":
    unittest.main()
