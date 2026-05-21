from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .core import (
    CodexExecutableNotFound,
    CodexerError,
    HookExists,
    ProfileExists,
    ProfileNotFound,
    SourceHomeMissing,
    add_profile,
    add_hook,
    list_hooks,
    list_profiles,
    open_profile,
    profile_path,
    remove_hook,
    remove_profile,
    run_codex,
)


ADD_COMMANDS = {"add", "new", "register"}
REMOVE_COMMANDS = {"rm", "remove", "del", "delete"}
LIST_COMMANDS = {"list", "ls"}
HOOK_COMMANDS = {"hook", "hooks"}


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
    if command in ADD_COMMANDS:
        return _add(args[1:])
    if command == "init":
        return _init(args[1:])
    if command in REMOVE_COMMANDS:
        return _rm(args[1:])
    if command in LIST_COMMANDS:
        return _list(args[1:])
    if command in HOOK_COMMANDS:
        return _hook(args[1:])
    if command.startswith("-"):
        return run_codex(args)

    path = profile_path(command)
    if path.is_dir():
        return run_codex(args[1:], profile=command)
    raise ProfileNotFound(f"Profile '{command}' does not exist: {path}")


def _add(args: Sequence[str]) -> int:
    parser = _add_parser("codexer add")
    parsed = parser.parse_args(args)

    result = _create_profile(parsed)
    _print_add_result(result, opened=parsed.open)
    return 0


def _init(args: Sequence[str]) -> int:
    parser = _add_parser("codexer init")
    parsed, codex_args = parser.parse_known_args(args)
    if codex_args[:1] == ["--"]:
        codex_args = codex_args[1:]

    result = _create_profile(parsed)
    _print_add_result(result, opened=parsed.open)
    return run_codex(codex_args, profile=parsed.name)


def _create_profile(parsed: argparse.Namespace):
    try:
        result = add_profile(
            parsed.name,
            include_auth=parsed.include_auth,
            exclude_config=parsed.exclude_config,
        )
    except ProfileExists:
        raise
    except SourceHomeMissing:
        raise
    if parsed.open:
        open_profile(parsed.name)
    return result


def _print_add_result(result, *, opened: bool) -> None:
    print(f"Created profile '{result.profile.name}' at {_display(result.profile.path)}")
    print(f"Linked {result.linked_files} root item(s) from {_display(result.source_home)}")
    if result.skipped_files:
        skipped = ", ".join(str(path).replace("\\", "/") for path in result.skipped_files)
        print(f"Skipped: {skipped}")
    if opened:
        print(f"Opened {_display(result.profile.path)}")


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


def _hook(args: Sequence[str]) -> int:
    if not args:
        raise CodexerError("Hook command is required: add, rm, or list.")

    command = args[0]
    if command in ADD_COMMANDS:
        return _hook_add(args[1:])
    if command in REMOVE_COMMANDS:
        return _hook_rm(args[1:])
    if command in LIST_COMMANDS:
        return _hook_list(args[1:])
    raise CodexerError(f"Unknown hook command: {command}")


def _hook_add(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer hook add")
    name, command_parts, profile = _parse_hook_add_args(args, parser)
    if not command_parts:
        parser.error("command is required")

    try:
        hook = add_hook(name, _command_from_parts(command_parts), profile=profile)
    except HookExists:
        raise
    scope = "all profiles" if hook.profile == "*" else f"profile '{hook.profile}'"
    print(f"Added hook '{hook.name}' for {scope}: {hook.command}")
    return 0


def _hook_rm(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer hook rm")
    parser.add_argument("--profile", default="*")
    parser.add_argument("name")
    parsed = parser.parse_args(args)

    result = remove_hook(parsed.name, profile=parsed.profile)
    scope = "all profiles" if result.profile == "*" else f"profile '{result.profile}'"
    if result.removed:
        print(f"Removed hook '{result.name}' for {scope}")
    else:
        print(f"Warning: hook '{result.name}' does not exist for {scope}")
    return 0


def _hook_list(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="codexer hook list")
    parser.add_argument("--profile")
    parsed = parser.parse_args(args)

    hooks = list_hooks(profile=parsed.profile)
    if not hooks:
        print("No codexer hooks found.")
        return 0
    for hook in hooks:
        scope = "*" if hook.profile == "*" else hook.profile
        print(f"{scope}\t{hook.name}\t{hook.command}")
    return 0


def _add_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("name")
    parser.add_argument("--include-auth", action="store_true")
    parser.add_argument("--exclude-config", action="store_true")
    parser.add_argument("--open", action="store_true")
    return parser


def _parse_hook_add_args(
    args: Sequence[str],
    parser: argparse.ArgumentParser,
) -> tuple[str, list[str], str]:
    remaining = list(args)
    profile = "*"
    index = 0
    while index < len(remaining):
        value = remaining[index]
        if value == "--profile":
            if index + 1 >= len(remaining):
                parser.error("--profile requires a value")
            profile = remaining[index + 1]
            del remaining[index : index + 2]
            continue
        if value.startswith("--profile="):
            profile = value.split("=", 1)[1]
            del remaining[index]
            continue
        index += 1

    if not remaining:
        parser.error("name is required")
    return remaining[0], remaining[1:], profile


def _command_from_parts(parts: Sequence[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _display(path: Path) -> str:
    return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
