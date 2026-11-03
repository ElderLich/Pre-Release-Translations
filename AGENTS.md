# Translation work across the linked workspaces

The user's default translation scope is Standard Pre-Release German (DE), and
Rush German (DE), Portuguese (PT), and Traditional Chinese (ZH-TW).

Work with both this repository and `D:\Modding\Super-Pre Translations`.
Read `docs/TRANSLATOR_WORKFLOW.md` and `docs/RUSH_PRE_RELEASE.md` before updates.
Verify the current upstream inventory before translating; preserve existing
uncommitted work and reviewed translations. Refresh Standard through the local
helper and use the authoritative Rush `RD Patch.cdb` revision.

Translate card names, descriptions, and in-game prompts in repository Workspace
CDBs and their corresponding local `raw2` CDBs. Keep both workspaces synchronized
using the existing merge workflow, reviewing any conflicting fields. Preserve
card IDs and gameplay data.

When new Chinese prompts occur, update `Tools/PromptPatcher.py` and the relevant
card-name bindings in `Shared/Mappings.csv`, then refresh local mapping copies.
Review changed upstream fields against their scripts and printed card text;
already translated text can become stale when prompt positions change.
For ZH-TW, inspect new source rows manually: the report's CJK exemption can leave
Simplified Chinese names and effects unreported. Preserve established Traditional
Chinese terminology rather than blindly converting every string.

Generate packages and reports through the existing builders. Verify SQLite
integrity, source inventory/gameplay parity, unresolved placeholders, and pending
translations for the requested locales. Record source discrepancies without
silently modifying upstream gameplay. Commit or push when requested.
