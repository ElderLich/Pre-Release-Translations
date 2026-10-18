# Daily automation

`Daily translation updates` is the only cron workflow. It runs once per day at
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
