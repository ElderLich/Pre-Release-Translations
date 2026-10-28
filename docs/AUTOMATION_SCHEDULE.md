# Daily automation

`Daily translation updates` schedules source imports once per day at
08:10 UTC and uses one lightweight job to choose the imports that need running.
It does not download or rebuild the card packages itself.

- A locale whose source import succeeded within the previous 24 hours skips its
  next scheduled import. This uses the start time of the latest successful
  `Sync <LANG> Workspace` run, including a successful run with no file changes.
  ZH-TW uses its combined `AutoPR ZH-TW` workflow.
- Translation uploads and release builds do not reset the import cooldown.
  A new ES release cannot postpone the job that brings new CN cards into ES.
- The upstream package's HTTP Last-Modified date bypasses that cooldown if a newer
  package is newer than the last successful import, even if ES was released more
  recently. If that date is unavailable, the cooldown remains bounded to 24 hours
  after an import; a manual workspace sync always checks immediately. Missing,
  failed or unreadable import history never marks the sources as current.
- Each locale is checked independently, so an English-only update cannot hide
  pending work in other languages. If all locales are fresh, none of their sync
  or release workflows is launched.
- Rush checks its recorded upstream and translation repository revisions and
  local inputs. New, removed or edited source cards trigger an all-locale rebuild.
- Already queued/running workflows are not dispatched again.

The one dispatcher job still uses a small amount of Actions time. Checking all
locales in that job avoids starting an idle scheduled job for every language.
The normal import/release jobs run only when needed.

## Checkout, publication and retries

Jobs fetch only the current commit. Release numbers come from remote tag names,
so selecting the next version does not download historical package contents.
All Standard and Rush jobs that write to the same branch share a queue with up
to 100 pending jobs. A language waits for its turn instead of racing another
language's push; the dispatcher and Cloudflare mirror remain independent.
Queued time is distinct from time spent running a build.

Each publishing attempt starts from the latest branch tip. Human pushes can
still require a retry; the job rebuilds against those edits and publishes its
commit and release tags atomically. ZH-TW follows the same retry pattern.

Standard downloads are verified and cached only for the current workflow run
and attempt, outside the checkout. A retry reuses its selected upstream input
and remembers the official download endpoint that worked. A new run checks
sources again. Cache reuse still verifies the file hash and format, and TLS
verification remains enabled.

Standard package comparison ignores ZIP timestamps and SQLite change counters.
When content changes, archive metadata and ordering are normalized. Unchanged
packages, metadata and version files retain their previous bytes, preventing
timestamp-only releases. Incomplete prompt translations fail before downloads
with the exact file, line and prompt key to fix.

Deleting Actions run history also removes the dispatcher's import timestamps.
The next daily check therefore refreshes locales whose successful imports can
no longer be confirmed; subsequent checks resume the normal cooldown.

## Reliable publication handoffs

Daily imports are dispatched with `GITHUB_TOKEN`. Their follow-on completion
events have not reliably started the next workflow, so successful bot-driven
jobs explicitly dispatch the next stage: workspace sync to the matching locale
release, then locale release to the Cloudflare mirror. Rush and ZH-TW dispatch
the mirror after their combined source/publication job succeeds. Successful
no-op jobs continue too, allowing a failed delivery to recover without changing
package content. The launcher does not start the mirror before its children end.

Each continuation is a separate short job with only `contents: read` and
`actions: write`. The helper accepts an exact, acyclic workflow allowlist and
requires this repository, trusted main, the bot actor and successful source work.
Human-triggered jobs retain their existing completion triggers. All mirror runs
still use the same serialized, verified current-main publisher; no second
uploader or personal token is introduced. Failed handoffs fail visibly and can
be retried from the failed job. Network retries can produce duplicate dispatches
after an ambiguous response, which the existing publisher serialization handles.

## Manual updates

Run `Sync EN Workspace`, `Sync ES Workspace`, or the equivalent locale workflow
to import the current CN cards and string keys immediately. `Release EN` and
`Release ES` package the existing translations; they do not refresh the workspace.
Translator pushes still trigger their releases.
Run `Update Rush pre-release cards` to refresh every Rush locale immediately.
The daily dispatcher also has an optional **force** switch to request all locales.

The Rush workflow clones the current `master` of the Rush source repository and
the current `main` of MDPro4_Data (only Data/locales), reconciles each workspace,
then commits the deterministic packages and reports. It rebuilds on top of the
latest branch after a push conflict so concurrent translator edits are preserved.

If a Rush translation is incomplete, the job successfully saves the refreshed
translator workspaces and reports. The Actions summary lists ready and pending
locales with the number of cards needing translation/review. Each completed
locale publishes independently; pending locales retain their last-good releases
and review baselines. EN and its TH fallback publish together. Retired cards can
still be removed from pending locales' packages, advancing their versions while
retaining the other published cards. The source receipt records completed and
pending locales separately, so unfinished translations still get daily source
checks. Actual source-fetch, database and build errors still fail the job.
