from __future__ import annotations

import os
import json
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class CodexerError(Exception):
    """Base exception for SDK callers."""


class InvalidProfileName(CodexerError, ValueError):
    pass


class ProfileExists(CodexerError, FileExistsError):
    pass


class ProfileNotFound(CodexerError, FileNotFoundError):
    pass


class SourceHomeMissing(CodexerError, FileNotFoundError):
    pass


class CodexExecutableNotFound(CodexerError, FileNotFoundError):
    pass


class HookExists(CodexerError, FileExistsError):
    pass


class HookFailed(CodexerError, RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    path: Path


@dataclass(frozen=True)
class AddResult:
    profile: Profile
    source_home: Path
    linked_files: int
    skipped_files: tuple[Path, ...]


@dataclass(frozen=True)
class RemoveResult:
    name: str
    path: Path
    removed: bool


@dataclass(frozen=True)
class Hook:
    name: str
    command: str
    profile: str = "*"


@dataclass(frozen=True)
class RemoveHookResult:
    name: str
    profile: str
    removed: bool


@dataclass(frozen=True)
class HookRunResult:
    hook: Hook
    returncode: int


ROOT_EXCLUDED_FILES = frozenset({Path("auth.json"), Path("config.toml")})
GLOBAL_HOOK_PROFILE = "*"


def codexer_root(root: str | os.PathLike[str] | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    return Path(os.environ.get("CODEXER_ROOT", Path.home() / ".codexer")).expanduser()


def codex_home(home: str | os.PathLike[str] | None = None) -> Path:
    if home is not None:
        return Path(home).expanduser()
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def validate_profile_name(name: str) -> str:
    if not name or name in {".", ".."}:
        raise InvalidProfileName("Profile name must not be empty, '.' or '..'.")
    if "/" in name or "\\" in name:
        raise InvalidProfileName("Profile name must not contain path separators.")
    if Path(name).name != name:
        raise InvalidProfileName("Profile name must be a single folder name.")
    return name


def profile_path(name: str, *, root: str | os.PathLike[str] | None = None) -> Path:
    return codexer_root(root) / validate_profile_name(name)


def list_profiles(*, root: str | os.PathLike[str] | None = None) -> list[Profile]:
    base = codexer_root(root)
    if not base.exists():
        return []
    return sorted(
        (Profile(path.name, path) for path in base.iterdir() if path.is_dir()),
        key=lambda profile: profile.name.casefold(),
    )


def hook_config_path(*, root: str | os.PathLike[str] | None = None) -> Path:
    return codexer_root(root) / "hooks.json"


def add_hook(
    name: str,
    command: str,
    *,
    profile: str = GLOBAL_HOOK_PROFILE,
    root: str | os.PathLike[str] | None = None,
) -> Hook:
    hook = Hook(validate_hook_name(name), command.strip(), _validate_hook_profile(profile))
    if not hook.command:
        raise InvalidProfileName("Hook command must not be empty.")

    data = _load_hook_data(root=root)
    hooks = data.setdefault("profiles", {}).setdefault(hook.profile, [])
    if any(item.get("name") == hook.name for item in hooks):
        raise HookExists(f"Hook '{hook.name}' already exists for profile '{hook.profile}'.")
    hooks.append({"name": hook.name, "command": hook.command})
    _save_hook_data(data, root=root)
    return hook


def remove_hook(
    name: str,
    *,
    profile: str = GLOBAL_HOOK_PROFILE,
    root: str | os.PathLike[str] | None = None,
) -> RemoveHookResult:
    hook_name = validate_hook_name(name)
    hook_profile = _validate_hook_profile(profile)
    data = _load_hook_data(root=root)
    hooks = data.setdefault("profiles", {}).setdefault(hook_profile, [])
    kept = [item for item in hooks if item.get("name") != hook_name]
    removed = len(kept) != len(hooks)
    data["profiles"][hook_profile] = kept
    if removed:
        _save_hook_data(data, root=root)
    return RemoveHookResult(hook_name, hook_profile, removed)


def list_hooks(
    *,
    profile: str | None = None,
    root: str | os.PathLike[str] | None = None,
) -> list[Hook]:
    data = _load_hook_data(root=root)
    profiles = data.get("profiles", {})
    if profile is not None:
        hook_profile = _validate_hook_profile(profile)
        return [_hook_from_item(hook_profile, item) for item in profiles.get(hook_profile, [])]

    hooks: list[Hook] = []
    for hook_profile in sorted(profiles):
        hooks.extend(_hook_from_item(hook_profile, item) for item in profiles[hook_profile])
    return hooks


def run_hooks(
    *,
    profile: str | None,
    env: Mapping[str, str],
    root: str | os.PathLike[str] | None = None,
) -> list[HookRunResult]:
    hooks = list_hooks(profile=GLOBAL_HOOK_PROFILE, root=root)
    if profile is not None:
        hooks.extend(list_hooks(profile=profile, root=root))

    results: list[HookRunResult] = []
    for hook in hooks:
        completed = subprocess.run(hook.command, shell=True, env=dict(env), check=False)
        result = HookRunResult(hook, completed.returncode)
        results.append(result)
        if completed.returncode != 0:
            hint = ""
            if os.name != "nt" and _looks_windowsy_hook_command(hook.command):
                hint = (
                    " Hint: this looks like a Windows hook command. On this platform hooks run under "
                    "/bin/sh; use shell syntax like 'export VAR=value' or a '.sh' script."
                )
            raise HookFailed(
                f"Hook '{hook.name}' failed with exit code {completed.returncode}.{hint}"
            )
    return results


def validate_hook_name(name: str) -> str:
    if not name or name in {".", ".."}:
        raise InvalidProfileName("Hook name must not be empty, '.' or '..'.")
    if "/" in name or "\\" in name:
        raise InvalidProfileName("Hook name must not contain path separators.")
    return name


def add_profile(
    name: str,
    *,
    include_auth: bool = False,
    include_config: bool = False,
    root: str | os.PathLike[str] | None = None,
    source_home: str | os.PathLike[str] | None = None,
) -> AddResult:
    source = codex_home(source_home).resolve()
    if not source.is_dir():
        raise SourceHomeMissing(f"Codex home does not exist: {source}")

    target = profile_path(name, root=root)
    if target.exists():
        raise ProfileExists(f"Profile already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()

    skipped: list[Path] = []
    linked = 0
    for dirpath, _, filenames in os.walk(source):
        source_dir = Path(dirpath)
        rel_dir = source_dir.relative_to(source)
        target_dir = target / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            rel_file = rel_dir / filename
            if _should_skip(rel_file, include_auth=include_auth, include_config=include_config):
                skipped.append(rel_file)
                continue
            source_file = source / rel_file
            link_path = target / rel_file
            os.symlink(_symlink_target(source_file, link_path), link_path)
            linked += 1

    return AddResult(
        profile=Profile(validate_profile_name(name), target),
        source_home=source,
        linked_files=linked,
        skipped_files=tuple(skipped),
    )


def remove_profile(name: str, *, root: str | os.PathLike[str] | None = None) -> RemoveResult:
    path = profile_path(name, root=root)
    if not path.exists():
        return RemoveResult(validate_profile_name(name), path, False)
    if not path.is_dir():
        raise ProfileNotFound(f"Profile path is not a directory: {path}")
    shutil.rmtree(path)
    return RemoveResult(validate_profile_name(name), path, True)


def open_profile(name: str, *, root: str | os.PathLike[str] | None = None) -> Path:
    path = profile_path(name, root=root)
    if not path.is_dir():
        raise ProfileNotFound(f"Profile does not exist: {path}")
    _open_path(path)
    return path


def build_codex_env(
    name: str,
    *,
    root: str | os.PathLike[str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    path = profile_path(name, root=root)
    if not path.is_dir():
        raise ProfileNotFound(f"Profile does not exist: {path}")
    env = dict(os.environ if base_env is None else base_env)
    env["CODEX_HOME"] = str(path)
    return env


def run_codex(
    args: Sequence[str],
    *,
    profile: str | None = None,
    root: str | os.PathLike[str] | None = None,
    executable: str = "codex",
    base_env: Mapping[str, str] | None = None,
    run_configured_hooks: bool = True,
) -> int:
    env = dict(os.environ if base_env is None else base_env)
    if profile is not None:
        env = build_codex_env(profile, root=root, base_env=env)
    else:
        env.setdefault("CODEX_HOME", str(codex_home()))
    if run_configured_hooks:
        hooks = _hooks_for_run(profile=profile, root=root)
    else:
        hooks = []
    executable_path = shutil.which(executable, path=env.get("PATH")) or executable
    if hooks:
        command = _shell_chain([hook.command for hook in hooks], [executable_path, *args])
        completed = subprocess.run(command, shell=True, env=env, check=False)
        return completed.returncode
    try:
        completed = subprocess.run([executable_path, *args], env=env, check=False)
    except (FileNotFoundError, PermissionError) as exc:
        raise CodexExecutableNotFound(f"Codex executable not found: {executable}") from exc
    return completed.returncode


def _should_skip(rel_file: Path, *, include_auth: bool, include_config: bool) -> bool:
    if rel_file == Path("auth.json") and not include_auth:
        return True
    if rel_file == Path("config.toml") and not include_config:
        return True
    return False


def _validate_hook_profile(profile: str) -> str:
    if profile == GLOBAL_HOOK_PROFILE:
        return profile
    return validate_profile_name(profile)


def _load_hook_data(*, root: str | os.PathLike[str] | None = None) -> dict[str, object]:
    path = hook_config_path(root=root)
    if not path.exists():
        return {"version": 1, "profiles": {}}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        return {"version": 1, "profiles": {}}
    profiles = data.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        data["profiles"] = {}
    data.setdefault("version", 1)
    return data


def _save_hook_data(data: Mapping[str, object], *, root: str | os.PathLike[str] | None = None) -> None:
    path = hook_config_path(root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")


def _hook_from_item(profile: str, item: Mapping[str, object]) -> Hook:
    return Hook(str(item.get("name", "")), str(item.get("command", "")), profile)


def _hooks_for_run(
    *,
    profile: str | None,
    root: str | os.PathLike[str] | None = None,
) -> list[Hook]:
    hooks = list_hooks(profile=GLOBAL_HOOK_PROFILE, root=root)
    if profile is not None:
        hooks.extend(list_hooks(profile=profile, root=root))
    return hooks


def _shell_chain(hook_commands: Sequence[str], codex_command: Sequence[str]) -> str:
    commands = [*hook_commands, _quote_shell_command(codex_command)]
    joiner = "&&" if os.name == "nt" else " && "
    return joiner.join(commands)


def _quote_shell_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return " ".join(_quote_cmd_arg(part) for part in command)
    return shlex.join(command)


def _quote_cmd_arg(value: str) -> str:
    if value == "":
        return '""'
    quoted = subprocess.list2cmdline([value])
    shell_special = set("&|<>()^")
    if any(char in shell_special for char in value) and not quoted.startswith('"'):
        quoted = f'"{quoted}"'
    return quoted


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys_platform() == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, str(path)])
    except FileNotFoundError as exc:
        raise CodexerError(
            f"Unable to open '{path}': opener '{opener}' was not found on PATH."
        ) from exc


def sys_platform() -> str:
    import sys

    return sys.platform


def _symlink_target(source_file: Path, link_path: Path) -> str:
    if os.name == "nt":
        return str(source_file)
    # Prefer relative symlinks on POSIX so profiles remain movable with their source home.
    return os.path.relpath(str(source_file), start=str(link_path.parent))


def _looks_windowsy_hook_command(command: str) -> bool:
    trimmed = command.strip()
    lowered = trimmed.casefold()
    if lowered.startswith("set ") and "=" in lowered:
        return True
    if ".cmd" in lowered or ".bat" in lowered:
        return True
    if "%codex_home%" in lowered or "%from_hook%" in lowered:
        return True
    return False
