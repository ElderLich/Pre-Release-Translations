# Daily automation

`Daily translation updates` is the only cron workflow. It runs once per day at
08:10 UTC and uses one lightweight job to choose the imports that need running.
It does not download or rebuild the card packages itself.

- A locale released within the previous 24 hours skips its next scheduled import.
  For example, an English release ten hours before the schedule skips the EN job.
- The upstream package's HTTP Last-Modified date bypasses that cooldown if a newer
  package is available. If that date is unavailable, the cooldown remains bounded
  to 24 hours; a manual workflow run always checks immediately.
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

Run the existing `Sync EN Workspace`, `Release EN`, or equivalent locale workflow
to force that locale immediately. Translator pushes still trigger their releases.
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
