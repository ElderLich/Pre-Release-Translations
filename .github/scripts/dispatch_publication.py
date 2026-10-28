"""Continue successful bot-driven publication through an explicit Actions event.

The daily dispatcher uses GITHUB_TOKEN, whose follow-on workflow_run events are
not reliable. Human-triggered runs keep their existing completion triggers.
This helper never builds or uploads packages and cannot select an arbitrary job.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = "ElderLich/Pre-Release-Translations"
LOCALES = ("de", "en", "es", "fr", "it", "jp", "kr", "pt", "th")
CONTINUATIONS = {f"{locale}-workspace-sync.yml": f"{locale}-release.yml" for locale in LOCALES}
CONTINUATIONS.update({f"{locale}-release.yml": "cloudflare-prerelease.yml" for locale in LOCALES})
CONTINUATIONS.update({"autopr-zh-tw.yml": "cloudflare-prerelease.yml",
                      "rush-prerelease.yml": "cloudflare-prerelease.yml"})
ATTEMPTS = 3
RETRYABLE_STATUSES = (408, 429, 500, 502, 503, 504)


class DispatchError(RuntimeError):
    """Controlled diagnostic: no token, response body or untrusted exception text."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DispatchError("GitHub dispatch returned a redirect; no credentials were forwarded.")


def continuation(environment, event):
    if (environment.get("GITHUB_REPOSITORY") != REPOSITORY or
            environment.get("GITHUB_ACTOR") != "github-actions[bot]" or
            environment.get("SOURCE_RESULT") != "success" or
            environment.get("GITHUB_REF") != "refs/heads/main"):
        return None
    event_name = environment.get("GITHUB_EVENT_NAME")
    if event_name == "workflow_run":
        previous = event.get("workflow_run", {})
        if (previous.get("conclusion") != "success" or
                previous.get("head_branch") != "main" or
                previous.get("head_repository", {}).get("full_name") != REPOSITORY):
            return None
    elif event_name not in ("push", "workflow_dispatch"):
        return None
    prefix = REPOSITORY + "/.github/workflows/"
    workflow_ref = environment.get("GITHUB_WORKFLOW_REF", "")
    if not workflow_ref.startswith(prefix) or not workflow_ref.endswith("@refs/heads/main"):
        return None
    source = workflow_ref[len(prefix):-len("@refs/heads/main")]
    return CONTINUATIONS.get(source)


def dispatch(target, token, opener=None, sleep=time.sleep):
    if target not in CONTINUATIONS.values():
        raise DispatchError("Unexpected continuation workflow.")
    if not token:
        raise DispatchError("The publication continuation has no Actions token.")
    opener = opener or build_opener(NoRedirects())
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/{target}/dispatches",
        data=json.dumps({"ref": "main"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "PreReleaseTranslations-PublicationContinuation/1"},
        method="POST",
    )
    for attempt in range(ATTEMPTS):
        try:
            with opener.open(request, timeout=30) as response:
                if response.status != 204:
                    raise DispatchError("GitHub did not acknowledge the publication continuation.")
            return
        except HTTPError as exc:
            retryable = exc.code in RETRYABLE_STATUSES
            diagnostic = f"GitHub dispatch returned HTTP {exc.code}."
            exc.close()
        except (URLError, TimeoutError, OSError):
            retryable = True
            diagnostic = "GitHub dispatch could not complete its network request."
        if not retryable or attempt == ATTEMPTS - 1:
            raise DispatchError(diagnostic + " Source publication is preserved; rerun this source workflow or its failed continuation job to retry delivery.")
        sleep(2 ** (attempt + 1))


def main():
    try:
        event = {}
        if os.environ.get("GITHUB_EVENT_NAME") == "workflow_run":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        target = continuation(os.environ, event)
        if target is None:
            print("No explicit continuation: requires a successful trusted main-branch bot publication.")
            return 0
        dispatch(target, os.environ.get("GH_TOKEN", ""))
        message = f"Requested {target} on current main after successful source publication."
        print(message)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("\n### Publication continuation\n\n" + message + "\n")
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::Could not read the trusted publication context or write its receipt. Source publication is preserved; rerun the failed continuation job.", file=sys.stderr)
        return 1
    except DispatchError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
