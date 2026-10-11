# Rush pre-release cards

Rush has its own packages and workspaces. The existing standard Super-Pre files
and translator helpers are unchanged.

## Run locally

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
an unrelated prompt change does not approve a changed effect description. The entire selected release batch fails before changing any YPK,
JSON, or version file until all required translations are ready. The GitHub
workflow still saves the workspaces/reports from that failed run so translators
can fix them. No untranslated placeholder package is published.

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

When a card leaves `RD Patch.cdb`, the next successful run removes it from every
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
the shared artwork, config, prompt patcher, mappings, tool, and workspace fingerprint before starting a
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
python -m unittest discover -s Tools/tests -p test_rush_prerelease.py -v
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
