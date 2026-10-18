# Rush pre-release cards

## Translator workflow

Open your usual locale `AutoPR.py` and select the **Rush** tab.
**Pull published** fetches the latest Pre-Release Translations commit and merges
its workspace into `<LANG>/Rush/raw2/rush-pre-release.cdb` in your local
translation folder. It reads the fetched commit even if local repository changes
prevent a fast-forward. **Fetch Rush updates** also fills missing EN/FR/JP fields
from the web using source snapshots inside Pre-Release Translations and requires
a clean `main` checkout. Use **Open Database in DataEditorX** to edit the local
file, then use **Upload workspace & push to GitHub** when your locale's translations are ready. The
existing GitHub workflow obtains Rush sources, translations and shared artwork
and builds all packages. Translators do not need local upstream repositories or
the maintainer's paths.

Local edits survive refreshes; upstream additions/removals/gameplay are merged
from the published workspace. Conflicting text changes stop for review in
`Rush/Reports/sync.json`. Thai text follows EN, and ZH-CN is upstream-managed;
those two workspaces are reference-only. Korean text remains translator-owned.
See [Translator Workflow](TRANSLATOR_WORKFLOW.md) for the full update/merge flow.

The source paths and all-locale commands below belong to the maintainer workflow
available under **All locales**. They are not translator prerequisites.


Rush has its own packages and workspaces. The standard Super-Pre package flow remains available alongside the locale Rush tools.

## Maintainer all-locale builds

Open any locale's `AutoPR.py` in `Super-Pre Translations`, then select **All locales**. The same window also keeps the standard MyCard, upload/push
and workspace pull actions on its **Standard** tab.

- **Fetch Updates & Build All Locales** fast-forwards the configured
  `ygopro-rush-duel` checkout from `origin/master`, then runs the existing Rush
  sync for all eleven locales. A dirty checkout, another branch, or local-only
  commits stop the fetch without stashing, resetting or discarding anything.
- **Build All Locales** rebuilds using the current local source files after
  translation, prompt or artwork edits. It can optionally skip web text sources.
- Select a locale in the report table to open its workspace or `report.json`.
  The **Shared Artworks** button opens the common artwork folder.

Source and locale database paths are remembered in `.transsuperpre_local.json`
beside the local language folders. Defaults are the sibling `ygopro-rush-duel`
and `MDPro4_Data/Data/locales` folders. A saved path to a missing sibling
`MDPro3_Data/Data/locales` is repaired when the renamed folder exists; existing
checkouts and custom paths are preserved. The window shows a live log and stays
responsive; duplicate Rush builds are blocked across locale windows.
Rush builds save locally and never commit or push. Review the generated outputs
in GitHub Desktop and publish when ready. Existing translation gates, removals,
Thai/English equality and exact inventory exclusions apply to both buttons.

The GUI lives in `Tools/prerelease_gui.py`; installed `AutoPR.py` wrappers load
it from the configured repository, so future GUI updates need no wrapper copy.
`AutoPR.py --menu` retains the old numbered interface.

## GitHub source downloads

The workflow and daily freshness check use the project's advertised HTTPS URL,
`https://code.moenext.com/mycard/ygopro-rush-duel.git`. `git.moenext.com` is its
SSH hostname; it does not serve the HTTPS clone used by GitHub runners.
`.github/scripts/fetch_rush_sources.py` fetches fresh shallow checkouts of Rush
and only the `Data/locales/*/rush_cards.cdb` seed databases, with three attempts.
Connections may stay below 1 KiB/s for up to 60 seconds before retrying.
Each clone and sparse database transfer has a five-minute limit, with 15- and
45-second pauses between attempts. Both repositories share a 15-minute download
budget so the 30-minute workflow retains time for packaging. Tags are skipped.
Timeouts terminate Git workers before partial attempts are discarded. Required
source files are checked, and existing checkouts are never overwritten. A source outage stops publication
instead of building packages from an old cached patch.

## Run locally from a terminal

From the Pre-Release Translations repository:

```powershell
python Tools/rush_prerelease.py sync --source-root "D:\Programs\GitHub\ygopro-rush-duel" --translations-root "D:\Programs\GitHub\MDPro4_Data\Data\locales"
```

The source checkout's `RD Patch.cdb` determines the active card IDs, gameplay
data, and original Chinese text. Update that checkout first when importing a
new upstream release. The command does not pull, push, or publish to GitHub.
Paths are arguments and do not need to match the maintainer's machine.

## Files to edit

```text
Shared/
  Rush Artworks/<card ID>.jpg or .png
  Rush/config.json
  Mappings.csv                            shared card-name bindings
  Rush/source.json                         generated source receipt
  Rush/sets.md                             generated upstream identity ranges

<LANG>/
  Rush/
    Workspace/rush-pre-release.cdb         translator input
    Base Files/rush-pre-release.cdb        generated original source
    report.json                           translation and artwork gaps
  rush-pre-release.ypk                     generated game package
  rush-pre-release.json                    generated card data
  rush-version.txt                         changes only with package content
```

Translate names, effect descriptions, and `str1` through `str16` in the
workspace CDB. Gameplay columns are always refreshed from the patch, so edit
those upstream. Existing workspace translations are preserved. Fields still blank or identical
to the original source are filled when newer seed translations become available.
The first import
reuses `rush_cards.cdb` from each published locale, including localized names in
effect prompts. Thai deliberately uses the English workspace and has the exact
same package bytes. Simplified Chinese uses the source text.

Rush uses the existing `Tools/PromptPatcher.py` catalog, including the Rush
strings already added there, and the existing `Shared/Mappings.csv` format.
This update adds 34 missing complete prompt templates covering 42 cells on 31
cards in the eight supported non-Chinese translation languages. Five shared
mapping rows bind referenced card names (or `self`) to each locale's translations.
The existing catalog entries and standard prerelease mappings are preserved.
Standard EN workspace cleanup now also preserves Rush mapping rows.

For future prompt gaps, add the translation to `PromptPatcher.py` as usual and
fill the card ID or `self` in the matching `strN` column of `Shared/Mappings.csv`.
Unresolved `{CARD}` placeholders stop publication and appear in `report.json`.
Translator corrections take precedence. Rush sync does not rewrite the published
base `rush_cards.cdb` files.

New untranslated cards stay in every workspace and appear in `report.json`.
Changed source descriptions/prompts require review when the corresponding
translated fields have not changed since the last successful publication.
Each completed locale publishes independently. A ready Spanish translation,
for example, updates its package immediately even while German or English still
needs review. An unrelated prompt change does not approve a changed effect
description. Pending locales retain their last published review baselines and
packages; their workspaces and reports are saved for translators to complete.
If upstream also retired cards, those IDs are pruned from each pending locale's
last-good package/JSON and its version advances. Retained cards keep their
previously published text and scripts. Untranslated new rows never enter those
retirement-only packages. Pure additions with translation gaps leave that
locale's previous release bytes untouched.

Thai uses the English fallback: selecting either EN or TH rebuilds both, and
Thai follows English translation/review readiness and has the same package bytes.
The Actions summary distinguishes published, already-current and pending locales.
Pending translation is a normal successful `sync` outcome; invalid databases,
missing scripts and other build failures still fail the job before releases are
written. The shared source receipt lists only ready locales as built and records
pending counts, so the daily dispatcher continues checking unfinished work.

## Automatic names and card effects

The normal `sync` command now fetches missing names/effects automatically for
English, French and Japanese before applying the existing PromptPatcher
and mappings. Pass `--offline` to use only the local workspaces and translated
seed databases. Existing translator edits take precedence; fetching never
adds card IDs beyond `RD Patch.cdb` or rewrites gameplay columns.

- English and French use [Yugipedia's MediaWiki API](https://yugipedia.com/api.php).
  French reads `fr_name`, `fr_text`, `fr_requirement`, `fr_materials` and
  `fr_condition`, not English fallbacks. If French fields are incomplete, the
  importer uses [CodexYGO's public API](https://codexygo.fr/api/card/v1/docs/?lang=en)
  with `format=rush`. When English names differ (such as Mad Rare/Mad Rhea or
  Praime/Prime), bounded searches using words from the title find candidates.
  Both the verified Yugipedia identity and Rush flag must still match.
  French requirements, materials, continuous effects, choices and Maximum
  ATK are preserved; partial translations stay pending.
- Korean is translator-managed. No Korean web text is fetched; existing Korean
  workspace translations and reviewed Korean locale seeds are preserved. New
  untranslated cards stay pending until the translator supplies them. Upstream
  inventory/gameplay updates, removals and translator-owned PromptPatcher entries
  still apply. `KR/Rush/source_text.json` explicitly reports manual mode.
- Japanese uses [ocg-card.com's Rush lists](https://ocg-card.com/list_r/).
  Examples are [Limited Pack Remastered Edition](https://ocg-card.com/list_r/lp01/)
  and [Cross Art Collection](https://ocg-card.com/list_r/ca01/). This is a
  community card-list site, not a Konami service. The fetcher preserves
  fusion materials, Maximum ATK, conditions and continuous/choice effects.
- Simulator IDs are matched using the explicit ID ranges in upstream `sets.md`.
  For example `120309XXX` maps to `RD/CA01-JPxxx`. Yugipedia redirects must land
  on a page that lists that exact printed set code; Japanese rows must carry
  the same code. Ambiguous and missing identities stay pending.
- For exceptions, add an entry to `Shared/Rush/source_mappings.json`:
  `"120309029": {"set_code": "RD/CA01-JP029", "evidence": "verified source URL"}`
  inside `cards`. A direct API lookup uses `action=query`, `titles=RD/CA01-JP029`,
  `redirects=1`, `prop=revisions`, `rvprop=content`, `format=json` and
  `formatversion=2` at the linked Yugipedia endpoint. `ocg_slugs` maps unusual set prefixes to site page paths.
  No identity is inferred from a similar card name or artwork.

`<LANG>/Rush/source_text.json` records fetched field sources and unresolved
matches, including missing localized fields, unverified identities and request
failures. `report.json` remains the publication gate. Required missing French or
Korean text blocks publication until a real translation is supplied. The daily
dispatcher retries pending reports even if the Rush upstream revision has not
changed; completed unchanged packages still skip their build.

A live check on 2026-10-11 found complete French source text for all 90 playable
patch cards: five from Yugipedia and 85 through the CodexYGO fallback. English
and Japanese had also matched all 90. These checks do not overwrite existing
translations. Korean automatic fetching is disabled. Future cards remain pending
if their sites have not published complete text; manual translations and locale
seed CDBs remain valid inputs.

The per-language Rush refresh performs the same missing-field lookup using
`Shared/Rush/sets.md` and `<LANG>/Rush/Base Files/rush-pre-release.cdb`. It needs
no source checkout or artwork folder. Its local `Rush/Reports/source_text.json`
records the latest source lookup; the package build report remains the last
published build state. Use `rush-refresh --offline` to skip web lookups.

All Rush output descriptions, including preserved workspace translations, use separate `[REQUIREMENT]` / `[EFFECT]` lines for Latin languages, localized
equivalents and one line per bullet option. Japanese, Korean and Chinese retain
native `【】` headings. Compact Latin brackets avoid the full-width glyph padding. Summoning requirements,
Maximum ATK, quoted `[L]`/`[R]` names and effect calculations remain intact.
Formatting changes preserve wording and are committed to workspaces only once
source-review checks pass; formatting alone cannot approve a changed effect.
Unknown wiki templates are reported as unavailable rather than silently losing
part of an effect.

## Shared artwork

Put prepared square Rush artwork crops in `Shared/Rush Artworks`, named after
the **game card ID**, such as `120307001.jpg`. Use one PNG or JPEG per ID. These
are artwork crops, not full card scans or Neuron CID filenames.

Every locale package contains the same images at
`Picture/Rush_Arts/<card ID>.jpg` (or `.png`). MDPro4's existing Rush artwork
loader checks game IDs before CIDs, so a new patch card works before the next
CID mapping update. Only images for the active patch are included. Removed
cards' shared source images remain available for reuse, but disappear from every
rebuilt YPK. Duplicate JPG/PNG files for one ID cause a clear error.

Initial import: 89 prepared 512×512 artworks. `120309029` (Nehime the Dimensional
Sworn Ally, Neuron CID 23921) still has no prepared artwork; the report keeps that
gap visible. The simulator name helper `120000011` does not require artwork.

## Exact inventory and removal

The current patch has 93 rows. The packages contain 91: `120307059` and
`120307060` are the two explicitly retired duplicate printings from the approved
sheet inventory, whose surviving IDs are `120231024` and `120238001`. These
reviewed exclusions are in `Shared/Rush/config.json`. New exclusions must be
deliberate; the tool never removes a card just because its name, alias, or
artwork matches another card.

When a card leaves `RD Patch.cdb`, the next reconciliation removes it from every
workspace, generated CDB/JSON, and YPK. Packages are rebuilt from scratch, so
unused card scripts and artwork cannot linger inside old archives. A script
still needed by another active card remains as a dependency. An empty valid
patch produces a valid empty card database and a new package version, allowing
clients to clear the previous pre-release cards.

Patch scripts, alias scripts, explicit Lua dependencies, and shared `RD*.lua`
modules are included. The generic `special.lua` initializer is owned by the
client and is not overridden. Missing required effect scripts stop the build.

## GitHub automation and game URLs

The dedicated Rush workflow builds all locales and commits outputs locally on
the Actions runner before its existing authorized workflow push. The daily
dispatcher checks pending locales, recorded source and translations revisions plus
the shared artwork, config, prompt patcher, mappings, tool, source importer/mappings, and workspace fingerprint before starting a
Rush build. An unchanged run has identical CDB, ZIP, JSON, and version bytes.

The game consumes these stable paths on the repository's `main` branch:

```text
https://raw.githubusercontent.com/ElderLich/Pre-Release-Translations/main/<LANG>/rush-pre-release.ypk
https://raw.githubusercontent.com/ElderLich/Pre-Release-Translations/main/<LANG>/rush-version.txt
```

Folders: `EN`, `DE`, `ES`, `FR`, `IT`, `JP`, `KR`, `PT`, `TH`, `ZH-CN`, `ZH-TW`.
The new links become downloadable after the changes are pushed. Local generation
does not make unpushed files available online.

## Checks

```powershell
python -m unittest discover -s Tools/tests -v
python -m unittest discover -s .github/scripts -p "test_*.py" -v
```

Tests cover source removals across locales, empty patches, exact exclusions,
preserved translator edits, Thai/English equality, missing-translation publication
blocking, script closure, duplicate artwork, source-text review, deterministic
no-op runs, and the daily dispatcher's input fingerprint.

## MDPro4 update settings

The normal Simplified Chinese prerelease source still uses the upstream
`PrereleasePackUrl` and `PrereleasePackVersionUrl` settings. The other ten
languages use `TranslatedPrereleasePackUrl` and
`TranslatedPrereleasePackVersionUrl` templates, with `{locale}` expanded to the
repository folder. Rush uses its separate `RushPrereleasePackUrl` and
`RushPrereleasePackVersionUrl` templates for all eleven locales. Existing custom
locale URLs can be retained through `PrereleasePackOverrides`, keyed by the
full locale code such as `en-US`. The normal and Rush packages and installed
version settings are independent.
