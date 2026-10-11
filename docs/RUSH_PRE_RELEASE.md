# Rush pre-release cards

## Translator workflow

Open your usual locale `AutoPR.py` and select the **Rush** tab. Fetch/pull uses
only the TransSuperpre repository and prepares
`<LANG>/Rush/raw2/rush-pre-release.cdb` in your local translation folder. Edit
that file, then copy or explicitly upload/push your locale's workspace. The
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

Open any locale's `AutoPR.py` in `Super-Pre Translations`, then select **All locales**. The same window also keeps the standard MyCard, copy, upload/push
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
and `MDPro3_Data/Data/locales` folders. The window shows a live log and stays
responsive; duplicate Rush builds are blocked across locale windows.
Rush builds save locally and never commit or push. Review the generated outputs
in GitHub Desktop and publish when ready. Existing translation gates, removals,
Thai/English equality and exact inventory exclusions apply to both buttons.

The GUI lives in `Tools/prerelease_gui.py`; installed `AutoPR.py` wrappers load
it from the configured repository, so future GUI updates need no wrapper copy.
`AutoPR.py --menu` retains the old numbered interface.

## Run locally from a terminal

From the TransSuperpre repository:

```powershell
python Tools/rush_prerelease.py sync --source-root "D:\Programs\GitHub\ygopro-rush-duel" --translations-root "D:\Programs\GitHub\MDPro3_Data\Data\locales"
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
Completing one locale review remains recorded while other locales are pending;
an unrelated prompt change does not approve a changed effect description. New or
changed card translations are held back across the selected release batch until
all required locales are ready. If upstream also retired cards, those IDs are
immediately pruned from every last-good package/JSON and the versions advance;
retained cards keep their previously published text and scripts. Untranslated
new rows never enter those retirement-only packages. The GitHub
workflow still saves the workspaces/reports from that failed run so translators
can fix them. No untranslated placeholder package is published. Pure additions with translation
gaps leave all previous release bytes untouched.

## Automatic names and card effects

The normal `sync` command now fetches missing names/effects automatically for
English, French and Japanese before applying the existing PromptPatcher
and mappings. Pass `--offline` to use only the local workspaces and translated
seed databases. Existing translator edits take precedence; fetching never
adds card IDs beyond `RD Patch.cdb` or rewrites gameplay columns.

- English and French use [Yugipedia's MediaWiki API](https://yugipedia.com/api.php).
  French reads `fr_name`, `fr_text`, `fr_requirement`, `fr_materials` and
  `fr_condition`, not English fallbacks.
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

A live check on 2026-10-11 found complete source text for all 90 playable patch
cards in English and Japanese, but only 5 in French. Korean automatic fetching
is disabled regardless of what external sites contain. The current
translated seed databases already fill all 91 rows, including the simulator
helper, so this source limitation does not remove current translations. It
matters for future new cards: an API cannot supply a translation that its site
has not published. Your manual translations and the published locale seed
CDBs remain valid inputs.

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
`Picture/Rush_Arts/<card ID>.jpg` (or `.png`). MDPro3's existing Rush artwork
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
dispatcher checks the last successful source and translations revisions plus
the shared artwork, config, prompt patcher, mappings, tool, source importer/mappings, and workspace fingerprint before starting a
Rush build. An unchanged run has identical CDB, ZIP, JSON, and version bytes.

The game consumes these stable paths on the repository's `main` branch:

```text
https://raw.githubusercontent.com/ElderLich/TransSuperpre/main/<LANG>/rush-pre-release.ypk
https://raw.githubusercontent.com/ElderLich/TransSuperpre/main/<LANG>/rush-version.txt
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

## MDPro3 update settings

The normal Simplified Chinese prerelease source still uses the upstream
`PrereleasePackUrl` and `PrereleasePackVersionUrl` settings. The other ten
languages use `TranslatedPrereleasePackUrl` and
`TranslatedPrereleasePackVersionUrl` templates, with `{locale}` expanded to the
repository folder. Rush uses its separate `RushPrereleasePackUrl` and
`RushPrereleasePackVersionUrl` templates for all eleven locales. Existing custom
locale URLs can be retained through `PrereleasePackOverrides`, keyed by the
full locale code such as `en-US`. The normal and Rush packages and installed
version settings are independent.
