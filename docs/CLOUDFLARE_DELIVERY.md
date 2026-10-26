# Cloudflare delivery for pre-release cards

GitHub remains the source of published translations. The Cloudflare workflow
mirrors the already-built Standard and Rush `.ypk` packages; it does not build
translations, import unfinished workspaces, create GitHub Releases, or change
the existing package/version URLs.

This integration is inactive until the repository variables and secrets below
are configured. A run without them explicitly reports **not configured** and
uploads nothing. The intended public host is `https://cards.elderlich.com`;
creating a workflow does not register that domain or connect it to a bucket.

## Set up the destination once

1. In Cloudflare R2, create a **Standard** bucket named `mdpro4-prerelease`, with
   Automatic location. Keep it separate from the `mdpro4-releases` game-patch
   bucket. Full game installers remain on their existing external mirrors.
2. Attach `cards.elderlich.com` through the bucket's **Settings → Custom Domains**
   after the domain is registered and active in this Cloudflare account. For a
   bounded initial test, the bucket's public `r2.dev` URL can be used instead.
   The S3 endpoint is an upload address, not the public download URL. Cloudflare
   reserves `r2.dev` for development and rate-limits it; use the custom domain
   before activating it for players. [Public bucket setup](https://developers.cloudflare.com/r2/buckets/public-buckets/).
3. Create R2 S3 credentials with **Object Read & Write** permission restricted
   to `mdpro4-prerelease`. Put the access key and secret directly in the GitHub
   repository's Actions secrets. Do not commit or paste them into documentation,
   game settings, workflow files, or chat. [R2 token permissions](https://developers.cloudflare.com/r2/api/tokens/).
4. In **GitHub → Pre-Release-Translations → Settings → Secrets and variables →
   Actions**, create the entries below. These are repository entries, not an
   additional GitHub Environment.

| Type | Name | Value |
| --- | --- | --- |
| Variable | `R2_ACCOUNT_ID` | The Cloudflare account ID shown on the R2 overview |
| Variable | `R2_PRERELEASE_BUCKET` | `mdpro4-prerelease` |
| Variable | `R2_PRERELEASE_PUBLIC_BASE_URL` | `https://cards.elderlich.com`, or the test bucket's public URL |
| Secret | `R2_PRERELEASE_ACCESS_KEY_ID` | Bucket-scoped S3 access key ID |
| Secret | `R2_PRERELEASE_SECRET_ACCESS_KEY` | Matching S3 secret access key |

The public base URL has no trailing package path or catalog filename. The
workflow derives the S3 endpoint from the account ID. Its GitHub token only
needs the declared `contents: read` permission; the mirror does not push commits
or tags. Credentials exist only in the publishing step's environment.

## First publication and activation

Open **Actions → Mirror pre-release packages to Cloudflare → Run workflow** and
select **main**. This backfills the complete currently published snapshot:
10 Standard packages and 11 Rush packages. Failed locale builds already retain
their previous published packages, so the mirror uploads those same last-good
files rather than pending translator inputs.

After a successful run, inspect the public catalog at:

```text
https://cards.elderlich.com/prerelease/catalog-v1.json
```

Check that all 21 expected entries exist and test an actual Standard and Rush
download using the advertised size and SHA-256. The publisher performs public
byte verification before promotion; game acceptance must also cover missing or
corrupt Cloudflare objects and the GitHub fallback. Leave the game's catalog
setting empty until the real public host and actual client are accepted. A
successful test on `r2.dev` does not establish the custom domain's readiness.

Switching from a test public URL to the custom domain requires updating
`R2_PRERELEASE_PUBLIC_BASE_URL` and rerunning the workflow so the catalog's
payload URLs use the new host. Existing GitHub downloads remain available.

## What triggers the mirror

The current build workflows commit package files and create **Git tags**, not
GitHub Release assets. The mirror follows successful `workflow_run` completion
for all current publishers:

- `Release DE`, `Release EN`, `Release ES`, `Release FR`, `Release IT`,
  `Release JP`, `Release KR`, `Release PT`, and `Release TH`.
- `AutoPR ZH-TW`.
- `Update Rush pre-release cards`.

It also handles human pushes of package/version files to main and manual runs.
The daily dispatcher starts the existing builders; their completion triggers
the mirror without another schedule or edits to all eleven workflows.

A tag-push or `release: published` trigger alone would miss normal automation:
the current publishers use `GITHUB_TOKEN`, whose pushes do not launch another
push workflow. [GitHub workflow-trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

The mirror accepts successful publishers from this repository's main branch
only. It never runs pull-request code with R2 credentials. One fixed concurrency
group serializes every entry point without cancelling an upload. Each job checks
out the latest main after entering that group, rather than an old event commit.
The publisher checks main before payload transfers and again before catalog
promotion. An already superseded snapshot is skipped before doing network work;
if main changes during publication, the catalog is not promoted. A newer pending
run or a manual rerun uses the current snapshot. A late publisher completion
therefore does not restore an older catalog. Payload verification failures name
the affected package and failed HTTP, size, hash or timeout check. Public verification
retries interrupted/truncated transfers, timeouts and HTTP 408/429/500/502/503/504
up to three total attempts, waiting two then four seconds. Each attempt starts a
fresh full download with its own 120-second transfer budget and 30-second socket
timeout. Size and SHA-256 checks run from byte zero on every attempt. Oversized
responses, wrong hashes, unsafe redirects and permanent HTTP failures stop
immediately. Exhausted retries preserve the previous catalog; all 21 packages
must still verify before the conditional promotion. Public catalog read-back
uses the same retry policy, without rolling back an already committed catalog.

## Object and catalog contract

```text
mdpro4-prerelease/
  prerelease/
    catalog-v1.json
    standard/EN/<sha256>/ygopro-super-pre.ypk
    rush/EN/<sha256>/rush-pre-release.ypk
    ... other available locales ...
```

The aggregate catalog has this shape (values below are illustrative):

```json
{
  "schemaVersion": 1,
  "sourceRepository": "ElderLich/Pre-Release-Translations",
  "sourceCommit": "<40-character Git commit>",
  "packs": [
    {
      "locale": "EN",
      "kind": "standard",
      "version": "1792256031",
      "fileName": "ygopro-super-pre.ypk",
      "sizeBytes": 123456,
      "sha256": "<64-character SHA-256>",
      "url": "https://cards.elderlich.com/prerelease/standard/EN/<sha256>/ygopro-super-pre.ypk",
      "mirrors": [
        "https://raw.githubusercontent.com/ElderLich/Pre-Release-Translations/<sourceCommit>/EN/ygopro-super-pre.ypk"
      ]
    }
  ]
}
```

Standard source files are `<locale>/ygopro-super-pre.ypk` and
`<locale>/version.txt`; Rush uses `<locale>/rush-pre-release.ypk` and
`<locale>/rush-version.txt`. Version files contain trimmed decimal Unix epoch
values. `test-release.json` and `rush-pre-release.json` are card metadata, not
delivery catalogs.

Standard locales: DE, EN, ES, FR, IT, JP, KR, PT, TH, ZH-TW. Rush includes those
ten plus ZH-CN. This repository has **no Standard ZH-CN package**. Its separate
upstream source remains unchanged; the mirror must not invent that entry.

The publisher validates the entire expected snapshot and its commit identity
before uploading. Immutable objects are keyed by their content hash; an existing
object must match the expected bytes. It uploads and verifies all packages
before conditionally replacing the aggregate catalog. Payload failures leave
the previous catalog available, and conflicts do not overwrite another writer.
The GitHub fallback is pinned to the same
source commit, so it delivers the exact bytes advertised by a cached catalog.

Treat packages as immutable cacheable files and the catalog as metadata that
must revalidate. Do not configure a long unconditional cache lifetime for
`prerelease/catalog-v1.json`. This workflow never deletes historical objects;
retire unused snapshots separately after checking supported clients and cached
catalogs.

## Validation and recovery

Run the workflow guard tests locally:

```text
python -B -m unittest discover -s .github/scripts -p test_cloudflare_workflow.py
python -B -m unittest discover -s .github/scripts -p test_publish_prerelease_r2.py
```

An incomplete setup reports the missing configuration names, not their values.
An upload or payload-verification failure fails the run before promoting a new
catalog. A promotion conflict preserves the current writer's catalog. The
publisher also verifies the stable public catalog URL after the conditional R2
write. If that final check fails, the diagnostic explicitly says the catalog
was committed to R2; inspect public access/cache rules before enabling the host.
It never blindly rolls back a committed catalog. Correct the reported condition
and run the same workflow again from main; it can reuse already verified
immutable objects. Do not make a new translation release merely to retry delivery.
