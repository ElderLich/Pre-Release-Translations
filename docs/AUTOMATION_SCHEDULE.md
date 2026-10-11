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
the current `main` of MDPro3_Data (only Data/locales), reconciles each workspace,
then commits the deterministic packages and reports. It rebuilds on top of the
latest branch after a push conflict so concurrent translator edits are preserved.

If a Rush translation is incomplete, the job saves the refreshed translator
workspaces and reports, then fails visibly. Existing packages, versions and the
last successful source receipt stay unchanged until every locale is complete.
