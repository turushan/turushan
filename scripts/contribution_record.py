"""Update the profile's contribution record without publishing activity dates."""

import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- contribution-record:start -->"
END = "<!-- contribution-record:end -->"


class RecordError(Exception):
    pass


def graphql(token, query, variables=None):
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-contribution-record",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            scopes = {s.strip() for s in response.headers.get("X-OAuth-Scopes", "").split(",")}
            if not scopes.intersection({"read:user", "user"}):
                raise RecordError("CONTRIBUTIONS_TOKEN needs the classic read:user scope for private totals.")
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RecordError(f"GitHub request failed (HTTP {error.code}); check the token and its expiry.") from None
    except (urllib.error.URLError, TimeoutError):
        raise RecordError("GitHub request failed; retry the workflow.") from None
    if payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise RecordError("GitHub returned an incomplete GraphQL response; no files changed.")
    return payload["data"]


def ranges_for(today, years, full_scan):
    yesterday = today - dt.timedelta(days=1)
    if full_scan:
        return [
            (dt.date(year, 1, 1), min(dt.date(year, 12, 31), yesterday))
            for year in sorted(set(years))
            if dt.date(year, 1, 1) <= yesterday
        ]
    return [(today - dt.timedelta(days=7), yesterday)]


def calendar_max(calendar, start, end):
    """Require every requested day so partial data never produces a success."""
    days = {}
    try:
        for week in calendar["weeks"]:
            for item in week["contributionDays"]:
                date = dt.date.fromisoformat(item["date"])
                if not start <= date <= end:
                    continue
                count = item["contributionCount"]
                if type(count) is not int or count < 0 or date in days:
                    raise ValueError()
                days[date] = count
        if len(days) != (end - start).days + 1:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise RecordError("GitHub returned an invalid or incomplete calendar; no files changed.") from None
    return max(days.values())


def updated_files(readme, state, candidate, scanned):
    if type(state.get("record")) is not int or state["record"] < 0:
        raise RecordError("Invalid saved record.")
    if type(state.get("history_scanned")) is not bool:
        raise RecordError("Invalid history scan flag.")
    if type(candidate) is not int or candidate < 0:
        raise RecordError("Invalid candidate record.")
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise RecordError("README must contain exactly one contribution-record marker pair.")
    record = max(state["record"], candidate)
    block = f"{START}\n🔥 **Single-day contribution record: {record:,}**\n{END}"
    updated, replacements = re.subn(re.escape(START) + r".*?" + re.escape(END), lambda _: block, readme, flags=re.S)
    if replacements != 1:
        raise RecordError("README markers are out of order.")
    return updated, {"record": record, "history_scanned": state["history_scanned"] or scanned}


def run(root=ROOT, today=None, api=graphql):
    token = os.environ.get("CONTRIBUTIONS_TOKEN", "").strip()
    if not token:
        raise RecordError("Add the CONTRIBUTIONS_TOKEN Actions secret with a classic read:user token. See .github/CONTRIBUTION_RECORD.md.")
    owner = os.environ.get("PROFILE_OWNER", "turushan")
    today = today or dt.datetime.now(dt.timezone.utc).date()
    state_path = root / ".github/contribution-record.json"
    readme_path = root / "README.md"
    state = json.loads(state_path.read_text())
    readme = readme_path.read_text()
    # Validate saved state and markers before spending API calls.
    updated_files(readme, state, 0, False)
    identity = api(token, "query { viewer { login contributionsCollection { contributionYears } } }")["viewer"]
    if identity["login"].lower() != owner.lower():
        raise RecordError("CONTRIBUTIONS_TOKEN belongs to a different account; no files changed.")
    years = identity["contributionsCollection"]["contributionYears"]
    if not isinstance(years, list) or not years or any(type(year) is not int or not 2008 <= year <= today.year for year in years):
        raise RecordError("GitHub returned invalid contribution years; no files changed.")
    # Weekly and manual scans recover missed runs and older backdated activity.
    full_scan = not state["history_scanned"] or today.weekday() == 6 or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    candidate = state["record"]
    query = """query($from: DateTime!, $to: DateTime!) {
      viewer { contributionsCollection(from: $from, to: $to) {
        contributionCalendar { weeks { contributionDays { date contributionCount } } }
      } }
    }"""
    for start, end in ranges_for(today, years, full_scan):
        data = api(token, query, {"from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"})
        calendar = data["viewer"]["contributionsCollection"]["contributionCalendar"]
        candidate = max(candidate, calendar_max(calendar, start, end))
    updated, new_state = updated_files(readme, state, candidate, full_scan)
    if updated != readme:
        readme_path.write_text(updated)
    if new_state != state:
        state_path.write_text(json.dumps(new_state, indent=2) + "\n")
    # Public logs, files, and commit messages contain no record date or day totals.
    print("Contribution record increased." if new_state["record"] > state["record"] else "Contribution record unchanged.")
    if full_scan:
        print("Full history scan complete.")


if __name__ == "__main__":
    try:
        run()
    except (RecordError, KeyError, TypeError, ValueError) as error:
        # Only our own errors are safe to print; API payloads can contain private data.
        print(str(error) if isinstance(error, RecordError) else "Invalid response or saved state; record update aborted.", file=sys.stderr)
        sys.exit(1)
