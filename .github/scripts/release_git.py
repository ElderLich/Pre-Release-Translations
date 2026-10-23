"""Select release numbers from remote tag names without fetching their history."""
import argparse
from pathlib import Path
import re
import subprocess


def remote_tags(remote="origin"):
    # --refs omits annotated tags' peeled ^{} entries. ls-remote transfers names
    # and object IDs only, unlike fetch --tags which downloads old packages too.
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", remote],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return [line.split("\t", 1)[1].removeprefix("refs/tags/")
            for line in result.stdout.splitlines() if "\trefs/tags/" in line]


def next_number(tags, prefix, exclude_from=None):
    pattern = re.compile(re.escape(prefix) + r"([0-9]+)\Z")
    numbers = [int(match.group(1)) for tag in tags
               if (match := pattern.fullmatch(tag))]
    if exclude_from is not None:
        numbers = [number for number in numbers if number < exclude_from]
    return max(numbers, default=0) + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="origin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-tags")
    number = commands.add_parser("next-number")
    number.add_argument("--prefix", required=True)
    number.add_argument("--exclude-from", type=int)
    number.add_argument("--tags-file", type=Path)
    args = parser.parse_args()
    try:
        tags = (args.tags_file.read_text(encoding="utf-8").splitlines()
                if getattr(args, "tags_file", None) else remote_tags(args.remote))
    except subprocess.CalledProcessError as error:
        parser.exit(1, f"Unable to list remote release tags: {error.stderr.strip()}\n")
    if args.command == "list-tags":
        for tag in tags:
            print(tag)
    else:
        print(next_number(tags, args.prefix, args.exclude_from))


if __name__ == "__main__":
    main()
