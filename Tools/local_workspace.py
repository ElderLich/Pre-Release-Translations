#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local helper for uploading translator workspace files to TransSuperpre.

The GitHub Actions workflows own the standard build. This helper copies the
local translator files into the repo Workspace folder, refreshes local raw2 files
from the latest upstream package, and optionally commits and pushes Workspace
changes. Rush translators use published per-locale workspaces; maintainers can
fetch source dependencies and build all locales locally. Placeholder
mappings are shared in repo-level Shared/Mappings.csv.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GH_REPO = os.environ.get("TRANSSUPERPRE_GH_REPO", "ElderLich/TransSuperpre")


@dataclass(frozen=True)
class LangConfig:
    key: str
    folder: str


LANGS = {
    "de": LangConfig("de", "DE"),
    "en": LangConfig("en", "EN"),
    "es": LangConfig("es", "ES"),
    "fr": LangConfig("fr", "FR"),
    "it": LangConfig("it", "IT"),
    "jp": LangConfig("jp", "JP"),
    "kr": LangConfig("kr", "KR"),
    "pt": LangConfig("pt", "PT"),
    "th": LangConfig("th", "TH"),
}

RUSH_LANGS = {**LANGS, "zh-cn": LangConfig("zh-cn", "ZH-CN"), "zh-tw": LangConfig("zh-tw", "ZH-TW")}

REQUIRED_WORKSPACE_FILES = ("test-release.cdb", "test-strings.conf")
LOCAL_ROOT_ENV = "SUPER_PRE_LOCAL_ROOT"
LOCAL_MAPPINGS_FILE = "Mappings.csv"


def child_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.environ.get("TRANSSUPERPRE_GUI") == "1" else 0


def log(message: str) -> None:
    print(f"[local-workspace] {message}")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def run(
    cmd: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(cwd), env=env, creationflags=child_creationflags())
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def repo_relative(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def looks_like_local_root(path: Path) -> bool:
    return any((path / config.folder / "raw2").is_dir() for config in RUSH_LANGS.values())


def discover_local_root(lang: str | None = None) -> Path | None:
    env_value = os.environ.get(LOCAL_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser()

    cwd = Path.cwd().resolve()
    candidates = [cwd, *cwd.parents]
    folder = RUSH_LANGS[lang].folder if lang in RUSH_LANGS else None
    for candidate in candidates:
        if folder and candidate.name.lower() == folder.lower() and (candidate / "raw2").is_dir():
            return candidate.parent
        if looks_like_local_root(candidate):
            return candidate
    return None


def resolve_local_root(value: str | None, lang: str) -> Path:
    if value:
        return Path(value).expanduser()
    discovered = discover_local_root(lang)
    if discovered:
        return discovered
    fail(
        "local translation root was not found. Pass --local-root, set SUPER_PRE_LOCAL_ROOT, "
        "or run through the locale AutoPR.py wrapper."
    )


def local_paths(
    args: argparse.Namespace,
    config: LangConfig,
    *,
    source_required: bool = True,
) -> tuple[Path, Path, Path]:
    local_root = resolve_local_root(args.local_root, config.key)
    repo_root = Path(args.repo_root).expanduser()
    local_dir = local_root / config.folder
    source_dir = local_dir / args.source_dir
    workspace_dir = repo_root / config.folder / "Workspace"
    if not local_dir.exists():
        fail(f"local locale folder not found: {local_dir}")
    if source_required and not source_dir.exists():
        fail(f"source folder not found: {source_dir}")
    if not repo_root.exists():
        fail(f"TransSuperpre repo not found: {repo_root}")
    return local_dir, source_dir, workspace_dir


def sync_shared_mappings_to_local(repo_root: Path, local_dir: Path, *, dry_run: bool = False) -> Path:
    source = repo_root / "Shared" / LOCAL_MAPPINGS_FILE
    target = local_dir / LOCAL_MAPPINGS_FILE
    if not source.exists():
        fail(f"shared mappings file not found: {source}")
    if dry_run:
        log(f"would copy {source} -> {target}")
    else:
        shutil.copy2(source, target)
        log(f"copied {source} -> {target}")
    return target


def copy_workspace(args: argparse.Namespace) -> list[Path]:
    config = LANGS[args.lang]
    local_dir, source_dir, workspace_dir = local_paths(args, config)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    changed_paths: list[Path] = []
    for filename in REQUIRED_WORKSPACE_FILES:
        source = source_dir / filename
        target = workspace_dir / filename
        if not source.exists():
            fail(f"required file missing: {source}")
        if args.dry_run:
            log(f"would copy {source} -> {target}")
        else:
            shutil.copy2(source, target)
            log(f"copied {source} -> {target}")
        changed_paths.append(target)

    return changed_paths


def pull_workspace_to_local(args: argparse.Namespace) -> list[Path]:
    config = LANGS[args.lang]
    local_dir, source_dir, workspace_dir = local_paths(args, config, source_required=False)
    if not workspace_dir.exists():
        fail(f"repo Workspace folder not found: {workspace_dir}")

    changed_paths: list[Path] = []
    source_dir.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_WORKSPACE_FILES:
        source = workspace_dir / filename
        target = source_dir / filename
        if not source.exists():
            fail(f"required repo workspace file missing: {source}")
        if args.dry_run:
            log(f"would copy {source} -> {target}")
        else:
            shutil.copy2(source, target)
            log(f"copied {source} -> {target}")
        changed_paths.append(target)

    return changed_paths


def ensure_no_staged_changes(repo_root: Path) -> None:
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo_root), creationflags=child_creationflags())
    if result.returncode != 0:
        fail("repo already has staged changes; commit or unstage them before using --push")


def ensure_main_branch(repo_root: Path) -> None:
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=repo_root,
                            capture_output=True, text=True, check=True, creationflags=child_creationflags()).stdout.strip()
    if branch != "main":
        fail(f"Switch TransSuperpre to main before publishing or refreshing (current branch: {branch or 'detached HEAD'}).")


def commit_and_push(args: argparse.Namespace, paths: list[Path]) -> None:
    repo_root = Path(args.repo_root).expanduser()
    ensure_main_branch(repo_root)
    ensure_no_staged_changes(repo_root)
    rel_paths = [repo_relative(repo_root, path) for path in paths]
    run(["git", "add", "-A", "--", *rel_paths], repo_root)

    diff_result = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *rel_paths], cwd=str(repo_root), creationflags=child_creationflags())
    if diff_result.returncode == 0:
        log("no new workspace changes to commit; retrying publication of main")
        run(["git", "push", "origin", "main"], repo_root)
        return
    if diff_result.returncode != 1:
        fail("Could not inspect staged workspace changes; nothing was committed or pushed.")

    message = args.message or f"Update {LANGS[args.lang].folder} workspace"
    run(["git", "commit", "-m", message], repo_root)
    run(["git", "push", "origin", "main"], repo_root)


def command_upload(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root).expanduser()
    if args.push:
        ensure_main_branch(repo_root)
    if args.pull_first:
        run(["git", "pull", "--ff-only", "origin", "main"], repo_root)
    changed_paths = copy_workspace(args)
    if args.push:
        commit_and_push(args, changed_paths)
    elif args.dry_run:
        log("dry run completed; no files were copied")
    else:
        log("copied files only; run with --push to commit and push")


def automation_script_command(repo_root: Path, lang: str) -> list[str]:
    scripts_dir = repo_root / ".github" / "scripts"
    if lang == "es":
        return [sys.executable, str(scripts_dir / "es_autopr.py"), "workspace-sync"]
    if lang in {"jp", "kr"}:
        return [sys.executable, str(scripts_dir / "ocg_autopr.py"), "--lang", lang, "workspace-sync"]
    if lang in {"de", "en", "fr", "it", "pt", "th"}:
        return [sys.executable, str(scripts_dir / "tcg_autopr.py"), "--lang", lang, "workspace-sync"]
    fail(f"local refresh is not configured for language: {lang}")


def automation_env_prefix(lang: str) -> str:
    if lang == "es":
        return "ES_AUTOPR"
    if lang in {"jp", "kr"}:
        return "OCG_AUTOPR"
    if lang in {"de", "en", "fr", "it", "pt", "th"}:
        return "TCG_AUTOPR"
    fail(f"local refresh is not configured for language: {lang}")


def command_refresh(args: argparse.Namespace) -> None:
    config = LANGS[args.lang]
    local_dir, source_dir, _workspace_dir = local_paths(args, config, source_required=False)
    repo_root = Path(args.repo_root).expanduser()
    source_dir.mkdir(parents=True, exist_ok=True)

    if args.pull_first:
        run(["git", "pull", "--ff-only", "origin", "main"], repo_root)

    prefix = automation_env_prefix(args.lang)
    env = os.environ.copy()
    env[f"{prefix}_OUTPUT_DIR"] = str(local_dir)
    env[f"{prefix}_BASE_DIR"] = str(local_dir / "Base Files")
    env[f"{prefix}_WORK_DIR"] = str(source_dir)
    env[f"{prefix}_TOOLS_DIR"] = str(repo_root / "Tools")
    env[f"{prefix}_MAPPINGS_PATH"] = str(repo_root / "Shared" / "Mappings.csv")

    run(automation_script_command(repo_root, args.lang), repo_root, env=env)
    sync_shared_mappings_to_local(repo_root, local_dir)
    log(f"refreshed local {config.folder}/{args.source_dir} from the latest upstream package")


def command_pull(args: argparse.Namespace) -> None:
    run(["git", "pull", "--ff-only", "origin", "main"], Path(args.repo_root).expanduser())
    if args.lang:
        changed_paths = pull_workspace_to_local(args)
        config = LANGS[args.lang]
        local_dir, _source_dir, _workspace_dir = local_paths(args, config, source_required=False)
        sync_shared_mappings_to_local(Path(args.repo_root).expanduser(), local_dir, dry_run=args.dry_run)
        if args.dry_run:
            log("dry run completed; no local files were copied")
        else:
            log(f"pulled latest {config.folder} workspace into local {args.source_dir}: files={len(changed_paths) + 1}")


def command_status(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root).expanduser()
    run(["git", "status", "--short", "--branch"], repo_root, check=False)
    command_actions(args)


def command_actions(args: argparse.Namespace) -> None:
    gh = shutil.which("gh")
    if not gh:
        log("GitHub CLI not found; open https://github.com/ElderLich/TransSuperpre/actions")
        return
    cmd = [
        gh,
        "run",
        "list",
        "--repo",
        args.gh_repo,
        "--limit",
        str(args.limit),
        "--json",
        "databaseId,name,status,conclusion,headSha,event,createdAt",
        "--jq",
        '.[] | [.databaseId,.name,.event,.status,(.conclusion//""),.headSha[0:7],.createdAt] | @tsv',
    ]
    log("$ " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(Path(args.repo_root).expanduser()), text=True, capture_output=True, creationflags=child_creationflags())
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        log("GitHub Actions status unavailable; check network/proxy/auth or open the Actions page manually")
        if result.stderr:
            print(result.stderr.strip())


def command_rush(args: argparse.Namespace) -> None:
    from rush_local import build_lock, fetch_source, python_console

    repo_root = Path(args.repo_root).expanduser().resolve()
    source = args.source_root.expanduser().resolve()
    translations = args.translations_root.expanduser().resolve()
    if not (repo_root / "Tools/rush_prerelease.py").is_file():
        fail(f"Rush builder not found in {repo_root}")
    if not (source / "RD Patch.cdb").is_file():
        fail(f"RD Patch.cdb not found in {source}")
    if not (translations / "en-US/rush_cards.cdb").is_file():
        fail(f"Expected the locale folder containing en-US/rush_cards.cdb: {translations}")
    cmd = [python_console(), str(repo_root / "Tools/rush_prerelease.py"), "sync",
           "--repo-root", str(repo_root), "--source-root", str(source),
           "--translations-root", str(translations), "--lang", "all"]
    if args.offline:
        cmd.append("--offline")
    try:
        with build_lock(repo_root):
            if args.fetch:
                fetch_source(source, log)
            log("Building all 11 Rush locales; existing translations and reviewed inventory exclusions are retained.")
            run(cmd, repo_root)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        fail(str(error))
    log("Rush packages built locally. Review and push with GitHub Desktop when ready.")



def pull_clean_translator_repo(repo_root: Path) -> None:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root,
                            capture_output=True, text=True, check=True, creationflags=child_creationflags())
    if status.stdout.strip():
        fail("TransSuperpre has uncommitted changes. Commit or review them first; Rush refresh does not stash, reset or discard work. "
             "Use --no-pull-first only when intentionally working with the current local repository.")
    ensure_main_branch(repo_root)
    run(["git", "pull", "--ff-only", "origin", "main"], repo_root)


def command_rush_refresh(args: argparse.Namespace) -> None:
    from rush_translator import refresh_locale
    repo_root = Path(args.repo_root).expanduser().resolve()
    local_root = resolve_local_root(args.local_root, args.lang).resolve()
    if args.pull_first:
        pull_clean_translator_repo(repo_root)
    try:
        refresh_locale(repo_root, local_root, RUSH_LANGS[args.lang].folder,
                       keep_local=args.keep_local, fetch_text=not args.offline, log=log)
    except (ValueError, OSError) as error:
        fail(str(error))


def command_rush_upload(args: argparse.Namespace) -> None:
    from rush_translator import copy_locale
    repo_root = Path(args.repo_root).expanduser().resolve()
    local_root = resolve_local_root(args.local_root, args.lang).resolve()
    if args.push and args.dry_run:
        fail("Use either --push or --dry-run, not both.")
    if args.push:
        # Check before copying so a failed publish cannot mutate a feature branch.
        ensure_main_branch(repo_root)
    if args.pull_first and not args.dry_run:
        pull_clean_translator_repo(repo_root)
    if args.push:
        ensure_no_staged_changes(repo_root)
    try:
        changed = copy_locale(repo_root, local_root, RUSH_LANGS[args.lang].folder, dry_run=args.dry_run, log=log)
    except (ValueError, OSError) as error:
        fail(str(error))
    if args.push:
        args.message = args.message or f"Update {RUSH_LANGS[args.lang].folder} Rush translations"
        commit_and_push(args, changed)
    elif not args.dry_run:
        log("Rush translations copied locally. Upload & push to trigger the GitHub build; no source/artwork repositories are required here.")


def command_rush_open(args: argparse.Namespace) -> None:
    local_root = resolve_local_root(args.local_root, args.lang)
    folder = local_root / RUSH_LANGS[args.lang].folder / "Rush" / ("Reports" if args.command == "rush-reports" else "raw2")
    if not folder.is_dir():
        fail(f"Rush folder does not exist yet: {folder}. Refresh Rush first.")
    if os.name == "nt":
        os.startfile(folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT), help="Path to the TransSuperpre repo")
    parser.add_argument(
        "--local-root",
        help="Path to the local translation workspace root. Defaults to SUPER_PRE_LOCAL_ROOT or auto-discovery from the current folder.",
    )
    parser.add_argument("--gh-repo", default=DEFAULT_GH_REPO, help="GitHub repo for gh run list")

    subparsers = parser.add_subparsers(dest="command", required=True)

    rush = subparsers.add_parser("rush", help="Build all Rush locales, optionally fetching upstream first")
    rush.add_argument("--source-root", type=Path, required=True)
    rush.add_argument("--translations-root", type=Path, required=True)
    rush.add_argument("--fetch", action="store_true", help="Fast-forward the clean Rush source checkout first")
    rush.add_argument("--offline", action="store_true", help="Skip web text sources when rebuilding")
    rush.set_defaults(func=command_rush)

    rush_refresh = subparsers.add_parser("rush-refresh", help="Refresh one locale from the published TransSuperpre Rush workspace")
    rush_refresh.add_argument("--lang", choices=sorted(RUSH_LANGS), required=True)
    rush_refresh.add_argument("--no-pull-first", action="store_false", dest="pull_first", help="Use the current local TransSuperpre checkout without fetching")
    rush_refresh.add_argument("--keep-local", action="store_true", help="After reviewing sync.json, explicitly retain local text when both sides changed")
    rush_refresh.add_argument("--offline", action="store_true", help="Refresh the published workspace without querying card text websites")
    rush_refresh.set_defaults(func=command_rush_refresh, pull_first=True)

    rush_upload = subparsers.add_parser("rush-upload", help="Copy one locale's Rush translations into the repository")
    rush_upload.add_argument("--lang", choices=sorted(RUSH_LANGS), required=True)
    rush_upload.add_argument("--push", action="store_true", help="Commit and push only this locale's Rush workspace; GitHub builds the packages")
    rush_upload.add_argument("--pull-first", action="store_true", help="Fast-forward a clean TransSuperpre checkout before merging")
    rush_upload.add_argument("--message")
    rush_upload.add_argument("--dry-run", action="store_true")
    rush_upload.set_defaults(func=command_rush_upload)

    for action in ("rush-open", "rush-reports"):
        rush_open = subparsers.add_parser(action, help="Open the selected locale's local Rush folder")
        rush_open.add_argument("--lang", choices=sorted(RUSH_LANGS), required=True)
        rush_open.set_defaults(func=command_rush_open)

    upload = subparsers.add_parser("upload", help="Copy local files into <lang>/Workspace")
    upload.add_argument("--lang", choices=sorted(LANGS), required=True)
    upload.add_argument("--source-dir", default="raw2", help="Local source folder inside the locale folder")
    upload.add_argument("--no-mappings", action="store_true", help="Accepted for compatibility; mappings are shared in Shared/Mappings.csv")
    upload.add_argument("--push", action="store_true", help="Commit and push copied Workspace files")
    upload.add_argument("--pull-first", action="store_true", help="Run git pull --ff-only before copying")
    upload.add_argument("--message", help="Commit message when --push is used")
    upload.add_argument("--dry-run", action="store_true", help="Show what would be copied")
    upload.set_defaults(func=command_upload)

    refresh = subparsers.add_parser(
        "refresh",
        help="Pull latest main, then download the latest .ypk and merge it into local raw2",
    )
    refresh.add_argument("--lang", choices=sorted(LANGS), required=True)
    refresh.add_argument("--source-dir", default="raw2", help="Local source folder inside the locale folder")
    refresh.add_argument("--pull-first", action="store_true", help="Run git pull --ff-only before refreshing")
    refresh.add_argument(
        "--no-pull-first",
        action="store_false",
        dest="pull_first",
        help="Do not pull before refreshing",
    )
    refresh.set_defaults(func=command_refresh, pull_first=True)

    pull = subparsers.add_parser("pull", help="Fast-forward local TransSuperpre main, then optionally copy <lang>/Workspace to local raw2")
    pull.add_argument("--lang", choices=sorted(LANGS), help="Language Workspace to copy back into the local folder")
    pull.add_argument("--source-dir", default="raw2", help="Local target folder inside the locale folder")
    pull.add_argument("--no-mappings", action="store_true", help="Accepted for compatibility; mappings are shared in Shared/Mappings.csv")
    pull.add_argument("--dry-run", action="store_true", help="Show what would be copied")
    pull.set_defaults(func=command_pull)

    status = subparsers.add_parser("status", help="Show git status and recent GitHub Actions")
    status.add_argument("--lang", choices=sorted(RUSH_LANGS), help="Accepted for wrapper compatibility")
    status.add_argument("--limit", type=int, default=8)
    status.set_defaults(func=command_status)

    actions = subparsers.add_parser("actions", help="Show recent GitHub Actions")
    actions.add_argument("--lang", choices=sorted(LANGS), help="Accepted for wrapper compatibility")
    actions.add_argument("--limit", type=int, default=12)
    actions.set_defaults(func=command_actions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
