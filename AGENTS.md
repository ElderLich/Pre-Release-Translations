# Translation work across the linked workspaces

The user's default translation scope is Standard Pre-Release German (DE), and
Rush German (DE), Portuguese (PT), and Traditional Chinese (ZH-TW).

A complete Rush refresh also resolves English and the other supported locales'
reported translation/review blockers. Thai follows the completed English pack.
The priority locales above are not a reason to leave shared prompt gaps blocking
other releases. Add new shared prompt templates for every supported patcher
language, review changed prompt-slot meanings, and verify all locale build
reports before calling a full Rush update complete. Inspect the latest imported
card names/effects as well: non-Chinese text in the wrong language, swapped cards,
and scraped page annotations may pass the pending-field detector.

Work with both this repository and `D:\Modding\Super-Pre Translations`.
Read `docs/TRANSLATOR_WORKFLOW.md` and `docs/RUSH_PRE_RELEASE.md` before updates.
Verify the current upstream inventory before translating; preserve existing
uncommitted work and reviewed translations. Refresh Standard through the local
helper and use the authoritative Rush `RD Patch.cdb` revision.

Translate card names, descriptions, and in-game prompts in repository Workspace
CDBs and their corresponding local `raw2` CDBs. Keep both workspaces synchronized
using the existing merge workflow, reviewing any conflicting fields. Preserve
card IDs and gameplay data.

When new Chinese prompts occur, update `Tools/PromptPatcher.py` for Standard
or `Tools/rush_prompt_catalog.json` for Rush. Update the relevant card-name
bindings in `Shared/Mappings.csv`, then refresh local mapping copies.
Review changed upstream fields against their scripts and printed card text;
already translated text can become stale when prompt positions change.
For ZH-TW, inspect new source rows manually: the report's CJK exemption can leave
Simplified Chinese names and effects unreported. Preserve established Traditional
Chinese terminology rather than blindly converting every string.

Generate packages and reports through the existing builders. Verify SQLite
integrity, source inventory/gameplay parity, unresolved placeholders, and pending
translations for the requested locales. Record source discrepancies without
silently modifying upstream gameplay.

Commit each logical repository change after validation with a descriptive subject
and body explaining the change and its checks. Push only when requested.
When the user requests local-only translations, keep the card databases and
packages in the local workspace; shared tooling fixes can still be committed.
