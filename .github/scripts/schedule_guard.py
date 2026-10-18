#!/usr/bin/env python3
"""Dispatch daily translation updates only when their sources need checking."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

from shared_downloads import YPK_URL_DEFAULT, download_urls
from fetch_rush_sources import RUSH_SOURCE, RUSH_TRANSLATIONS

UPSTREAM_YPK = YPK_URL_DEFAULT
LOCALES = ("DE", "EN", "ES", "FR", "IT", "JP", "KR", "PT", "TH", "ZH-TW")
RUSH_LOCALES = frozenset((*LOCALES, "ZH-CN"))


def cooldown_decision(event: str, checked: int | None, now: float,
                      hours: float = 24, upstream_modified: float | None = None) -> tuple[bool, str]:
    if event != "schedule":
        return True, "Manual and push runs bypass the scheduled cooldown."
    if checked is None or checked <= 0 or checked > now:
        return True, "No valid successful import timestamp; checking sources."
    age = now - checked
    if age >= hours * 3600:
        return True, f"Last successful import is at least {hours:g} hours old."
    if upstream_modified is not None and upstream_modified > checked:
        return True, "Upstream package changed after the latest import."
    return False, f"Sources checked {age / 3600:.1f} hours ago; next scheduled import waits until {hours:g} hours."


def latest_import(workflow: str, branch: str) -> int | None:
    """A translation release does not prove that the CN workspace was refreshed."""
    try:
        query = urlencode({"branch": branch, "status": "success", "per_page": 1})
        runs = github_api(f"actions/workflows/{workflow}/runs?{query}").get("workflow_runs", [])
        if not runs:
            return None
        run = runs[0]
        if run.get("conclusion") != "success" or run.get("head_branch") != branch:
            return None
        # Use the start, not completion: sources might change while a job is running.
        value = run.get("run_started_at")
        if not isinstance(value, str):
            return None
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(started.timestamp()) if started.tzinfo else None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"{workflow}: cannot confirm last successful import ({type(exc).__name__}); checking sources.")
        return None


def upstream_modified(url: str) -> float | None:
    for candidate in download_urls(url):
        try:
            request = urllib.request.Request(candidate, method="HEAD", headers={"User-Agent": "PreReleaseTranslations-ScheduleGuard/1"})
            with urllib.request.urlopen(request, timeout=10) as response:
                value = response.headers.get("Last-Modified")
            if value:
                return parsedate_to_datetime(value).timestamp()
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            print(f"Upstream date unavailable from {candidate}: {type(exc).__name__}")
    print("No upstream date available; keeping the bounded cooldown.")
    return None


def github_api(path: str, payload: dict | None = None) -> dict:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repository = os.environ["GITHUB_REPOSITORY"]
    request = urllib.request.Request(
        f"{api}/repos/{repository}/{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Content-Type": "application/json", "User-Agent": "PreReleaseTranslations-ScheduleGuard/1"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read()
    return json.loads(body) if body else {}


def dispatch_workflow(workflow: str, branch: str) -> bool:
    query = urlencode({"branch": branch, "per_page": 20})
    runs = github_api(f"actions/workflows/{workflow}/runs?{query}").get("workflow_runs", [])
    if any(run.get("status") != "completed" for run in runs):
        print(f"{workflow}: already queued or running; no duplicate dispatch.")
        return False
    github_api(f"actions/workflows/{workflow}/dispatches", {"ref": branch})
    print(f"{workflow}: dispatched.")
    return True


def rush_needs_update() -> tuple[bool, str]:
    receipt = Path("Shared/Rush/source.json")
    if not receipt.is_file():
        return True, "Rush source receipt is missing."
    try:
        state = json.loads(receipt.read_text(encoding="utf-8"))
        if set(state.get("built_locales", [])) != RUSH_LOCALES:
            return True, "Rush receipt does not cover every locale."
        for locale in RUSH_LOCALES:
            report_path = Path(locale) / "Rush/report.json"
            if report_path.is_file() and json.loads(report_path.read_text(encoding="utf-8")).get("pending_translations"):
                return True, f"Rush {locale} still has pending translations; retrying daily sources."
        for remote, ref, field in ((RUSH_SOURCE, "master", "source_revision"),
                                   (RUSH_TRANSLATIONS, "main", "translations_revision")):
            output = subprocess.check_output(["git", "ls-remote", remote, f"refs/heads/{ref}"],
                                             text=True, timeout=20).strip()
            if not output or output.split()[0] != state.get(field):
                return True, f"Rush {field} changed."
        sys.path.insert(0, str(Path("Tools").resolve()))
        from rush_prerelease import local_inputs_sha256
        if local_inputs_sha256(Path.cwd()) != state.get("local_inputs_sha256"):
            return True, "Rush translations, shared artwork or builder changed."
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return True, f"Cannot confirm Rush source freshness ({type(exc).__name__}); checking it."
    return False, "Rush source revisions and local inputs are unchanged."


def dispatch_daily(force: bool, cooldown_hours: float) -> None:
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    modified = upstream_modified(UPSTREAM_YPK)
    dispatched = []
    for locale in LOCALES:
        workflow = "autopr-zh-tw.yml" if locale == "ZH-TW" else f"{locale.lower()}-workspace-sync.yml"
        should_run, reason = cooldown_decision(
            "workflow_dispatch" if force else "schedule", None if force else latest_import(workflow, branch),
            time.time(), cooldown_hours, modified,
        )
        print(f"{locale}: {reason}")
        if should_run and dispatch_workflow(workflow, branch):
            dispatched.append(locale)
    rush_changed, rush_reason = rush_needs_update()
    print(rush_reason)
    if (force or rush_changed) and dispatch_workflow("rush-prerelease.yml", branch):
        dispatched.append("Rush (all locales)")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Daily dispatcher\n\nStarted locales: " + (", ".join(dispatched) or "none; already fresh") + ".\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cooldown-hours", type=float, default=24)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.cooldown_hours <= 0:
        parser.error("--cooldown-hours must be positive")
    dispatch_daily(args.force, args.cooldown_hours)


if __name__ == "__main__":
    main()
