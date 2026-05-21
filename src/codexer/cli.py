from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .core import (
    CodexExecutableNotFound,
    CodexerError,
    ProfileExists,
    ProfileNotFound,
    SourceHomeMissing,
    add_profile,
    list_profiles,
    open_profile,
    profile_path,
    remove_profile,
    run_codex,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return dispatch(args)
    except CodexerError as exc:
        print(f"codexer: error: {exc}", file=sys.stderr)
        return 2


def dispatch(args: list[str]) -> int:
    if not args:
        return run_codex([])

    command = args[0]
    if command == "add":
        return _add(args[1:])
    if command == "rm":
        return _rm(args[1:])
    if command == "list":
        return _list(args[1:])
    if command.startswith("-"):
        return run_codex(args)

    path = profile_path(command)
    if path.is_dir():
        return run_codex(args[1:], profile=command)
    raise ProfileNotFound(f"Profile '{command}' does not exist: {path}")


def _add(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer add")
    parser.add_argument("name")
    parser.add_argument("--include-auth", action="store_true")
    parser.add_argument("--include-config", action="store_true")
    parser.add_argument("--open", action="store_true")
    parsed = parser.parse_args(args)

    try:
        result = add_profile(
            parsed.name,
            include_auth=parsed.include_auth,
            include_config=parsed.include_config,
        )
    except ProfileExists:
        raise
    except SourceHomeMissing:
        raise

    print(f"Created profile '{result.profile.name}' at {_display(result.profile.path)}")
    print(f"Linked {result.linked_files} file(s) from {_display(result.source_home)}")
    if result.skipped_files:
        skipped = ", ".join(str(path).replace("\\", "/") for path in result.skipped_files)
        print(f"Skipped: {skipped}")
    if parsed.open:
        open_profile(parsed.name)
        print(f"Opened {_display(result.profile.path)}")
    return 0


def _rm(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer rm")
    parser.add_argument("name")
    parsed = parser.parse_args(args)

    result = remove_profile(parsed.name)
    if result.removed:
        print(f"Removed profile '{result.name}' at {_display(result.path)}")
    else:
        print(f"Warning: profile '{result.name}' does not exist at {_display(result.path)}")
    return 0


def _list(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer list")
    parser.parse_args(args)

    profiles = list_profiles()
    if not profiles:
        print("No codexer profiles found.")
        return 0
    for profile in profiles:
        print(f"{profile.name}\t{_display(profile.path)}")
    return 0


def _display(path: Path) -> str:
    return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
