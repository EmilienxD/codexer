from __future__ import annotations

import os
import json
import shutil
import shlex
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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
    background: bool = False
    log_file: str | None = None


@dataclass(frozen=True)
class RemoveHookResult:
    name: str
    profile: str
    removed: bool


@dataclass(frozen=True)
class HookRunResult:
    hook: Hook
    returncode: int


@dataclass(frozen=True)
class _BackgroundHookRun:
    hook: Hook
    process: subprocess.Popen
    log_handle: object


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
    return codexer_root(root) / "profiles" / validate_profile_name(name)


def list_profiles(*, root: str | os.PathLike[str] | None = None) -> list[Profile]:
    profiles_dir = codexer_root(root) / "profiles"
    if not profiles_dir.exists():
        return []
    return sorted(
        (Profile(path.name, path) for path in profiles_dir.iterdir() if path.is_dir()),
        key=lambda profile: profile.name.casefold(),
    )


def hook_config_path(*, root: str | os.PathLike[str] | None = None) -> Path:
    return codexer_root(root) / "hooks.json"


def add_hook(
    name: str,
    command: str,
    *,
    profile: str = GLOBAL_HOOK_PROFILE,
    background: bool = False,
    log_file: str | os.PathLike[str] | None = None,
    root: str | os.PathLike[str] | None = None,
) -> Hook:
    hook = Hook(
        validate_hook_name(name),
        command.strip(),
        _validate_hook_profile(profile),
        background=background,
        log_file=str(log_file) if log_file is not None else None,
    )
    if not hook.command:
        raise InvalidProfileName("Hook command must not be empty.")

    data = _load_hook_data(root=root)
    hooks = data.setdefault("profiles", {}).setdefault(hook.profile, [])
    if any(item.get("name") == hook.name for item in hooks):
        raise HookExists(f"Hook '{hook.name}' already exists for profile '{hook.profile}'.")
    hooks.append(_hook_to_item(hook))
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
        if hook.background:
            background = _start_background_hook(hook, profile=profile, env=dict(env), root=root)
            _stop_background_hook(background)
            results.append(HookRunResult(hook, 0))
            continue
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
    sym_auth: bool = False,
    sym_config: bool = False,
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
    for source_path in source.iterdir():
        rel_path = source_path.relative_to(source)
        link_path = target / rel_path
        if (
            _should_copy(rel_path, sym_auth=sym_auth, sym_config=sym_config)
            and source_path.is_file()
        ):
            shutil.copy2(source_path, link_path)
        else:
            os.symlink(
                _symlink_target(source_path, link_path),
                link_path,
                target_is_directory=source_path.is_dir(),
            )
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
    background_runs: list[_BackgroundHookRun] = []
    foreground_hooks = [hook for hook in hooks if not hook.background]
    background_hooks = [hook for hook in hooks if hook.background]
    try:
        if foreground_hooks and background_hooks:
            return _run_shell_plan(
                foreground_hooks,
                background_hooks,
                [executable_path, *args],
                profile=profile,
                env=env,
                root=root,
            )

        for hook in background_hooks:
            background_runs.append(_start_background_hook(hook, profile=profile, env=env, root=root))

        if foreground_hooks:
            command = _shell_chain([hook.command for hook in foreground_hooks], [executable_path, *args])
            completed = subprocess.run(command, shell=True, env=env, check=False)
            return completed.returncode
        try:
            completed = subprocess.run([executable_path, *args], env=env, check=False)
        except KeyboardInterrupt:
            return 130
        except (FileNotFoundError, PermissionError) as exc:
            raise CodexExecutableNotFound(f"Codex executable not found: {executable}") from exc
        return completed.returncode
    finally:
        for background in reversed(background_runs):
            _stop_background_hook(background)


def _should_copy(rel_file: Path, *, sym_auth: bool, sym_config: bool) -> bool:
    if rel_file == Path("auth.json"):
        return not sym_auth
    if rel_file == Path("config.toml"):
        return not sym_config
    return False


def _start_background_hook(
    hook: Hook,
    *,
    profile: str | None,
    env: Mapping[str, str],
    root: str | os.PathLike[str] | None = None,
) -> _BackgroundHookRun:
    log_path = _background_log_path(hook, profile=profile, root=root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(
        hook.command,
        shell=True,
        env=dict(env),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        **kwargs,
    )
    return _BackgroundHookRun(hook, process, log_handle)


def _run_shell_plan(
    foreground_hooks: Sequence[Hook],
    background_hooks: Sequence[Hook],
    codex_command: Sequence[str],
    *,
    profile: str | None,
    env: Mapping[str, str],
    root: str | os.PathLike[str] | None = None,
) -> int:
    with tempfile.TemporaryDirectory(prefix="codexer-") as temp:
        temp_path = Path(temp)
        if os.name == "nt":
            return _run_windows_shell_plan(
                temp_path=temp_path,
                foreground_hooks=foreground_hooks,
                background_hooks=background_hooks,
                codex_command=codex_command,
                profile=profile,
                env=env,
                root=root,
            )

        script = _write_posix_shell_plan(
            temp_path,
            foreground_hooks,
            background_hooks,
            codex_command,
            profile=profile,
            root=root,
        )
        completed = subprocess.run([str(script)], env=env, check=False)
        return completed.returncode


def _run_windows_shell_plan(
    *,
    temp_path: Path,
    foreground_hooks: Sequence[Hook],
    background_hooks: Sequence[Hook],
    codex_command: Sequence[str],
    profile: str | None,
    env: Mapping[str, str],
    root: str | os.PathLike[str] | None = None,
) -> int:
    hook_env = _run_windows_foreground_hooks_for_env(
        temp_path,
        foreground_hooks,
        env=env,
    )
    background_runs: list[_BackgroundHookRun] = []
    try:
        for hook in background_hooks:
            background_runs.append(
                _start_background_hook(hook, profile=profile, env=hook_env, root=root)
            )
        try:
            completed = subprocess.run(list(codex_command), env=hook_env, check=False)
        except KeyboardInterrupt:
            return 130
        except (FileNotFoundError, PermissionError) as exc:
            raise CodexExecutableNotFound(f"Codex executable not found: {codex_command[0]}") from exc
        return completed.returncode
    finally:
        for background in reversed(background_runs):
            _stop_background_hook(background)


def _run_windows_foreground_hooks_for_env(
    temp_path: Path,
    foreground_hooks: Sequence[Hook],
    *,
    env: Mapping[str, str],
) -> dict[str, str]:
    env_file = temp_path / "environment.txt"
    script = temp_path / "foreground-hooks.cmd"
    lines = ["@echo off"]
    for hook in foreground_hooks:
        lines.append(hook.command)
        lines.append("if errorlevel 1 exit /b %ERRORLEVEL%")
    lines.append(f'set > "{env_file}"')
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    completed = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", str(script)],
        env=_windows_minimum_env(env),
        check=False,
    )
    if completed.returncode != 0:
        failed = next((hook for hook in foreground_hooks), foreground_hooks[0])
        raise HookFailed(f"Hook '{failed.name}' failed with exit code {completed.returncode}.")
    return _read_windows_env_file(env_file, fallback=env)


def _read_windows_env_file(path: Path, *, fallback: Mapping[str, str]) -> dict[str, str]:
    parsed = dict(fallback)
    if not path.exists():
        return parsed
    text = path.read_text(encoding="mbcs", errors="replace")
    for line in text.splitlines():
        if not line or line.startswith("=") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        parsed[name] = value
    return parsed


def _write_posix_shell_plan(
    temp_path: Path,
    foreground_hooks: Sequence[Hook],
    background_hooks: Sequence[Hook],
    codex_command: Sequence[str],
    *,
    profile: str | None,
    root: str | os.PathLike[str] | None = None,
) -> Path:
    lines = [
        "#!/bin/sh",
        "pids=''",
        "cleanup() {",
        "  status=$?",
        "  for pid in $pids; do",
        "    kill -TERM -$pid >/dev/null 2>&1 || kill -TERM $pid >/dev/null 2>&1 || true",
        "  done",
        "  wait >/dev/null 2>&1 || true",
        "  exit $status",
        "}",
        "trap cleanup EXIT INT TERM",
    ]
    for hook in foreground_hooks:
        lines.append(f"{hook.command} || exit $?")
    for hook in background_hooks:
        log_path = _background_log_path(hook, profile=profile, root=root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = f"( {hook.command} ) >> {shlex.quote(str(log_path))} 2>&1"
        lines.append(f"sh -c {shlex.quote(command)} &")
        lines.append('pids="$pids $!"')
    lines.append(_quote_shell_command(codex_command))
    script = temp_path / "run.sh"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | 0o700)
    return script


def _stop_background_hook(background: _BackgroundHookRun) -> None:
    process = background.process
    try:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                import signal

                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal

                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                process.wait(timeout=5)
    finally:
        background.log_handle.close()


def _cleanup_logs(root: str | os.PathLike[str] | None = None, max_bytes: int = 50 * 1024 * 1024) -> None:
    logs_dir = codexer_root(root) / "logs"
    if not logs_dir.is_dir():
        return

    log_files = list(logs_dir.glob("*.log"))
    if not log_files:
        return

    log_stats = []
    for f in log_files:
        try:
            stat = f.stat()
            log_stats.append((f, stat.st_size, stat.st_mtime))
        except OSError:
            pass

    total_size = sum(size for _, size, _ in log_stats)
    if total_size <= max_bytes:
        return

    open_file_paths = set()
    try:
        import psutil
        for proc in psutil.process_iter(["open_files"]):
            try:
                files = proc.info.get("open_files")
                if files:
                    for f in files:
                        try:
                            open_file_paths.add(Path(f.path).resolve())
                        except (OSError, ValueError):
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass

    log_stats.sort(key=lambda x: x[2])

    for f, file_size, _ in log_stats:
        if total_size <= max_bytes:
            break
        try:
            resolved_path = f.resolve()
        except (OSError, ValueError):
            resolved_path = f
        if resolved_path in open_file_paths:
            continue
        try:
            f.unlink()
            total_size -= file_size
        except (PermissionError, OSError, FileNotFoundError):
            pass


def _background_log_path(
    hook: Hook,
    *,
    profile: str | None,
    root: str | os.PathLike[str] | None = None,
) -> Path:
    if hook.log_file is not None:
        return Path(os.path.expandvars(hook.log_file)).expanduser()
    scope = hook.profile
    if scope == GLOBAL_HOOK_PROFILE:
        scope = profile or "global"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    unique = uuid.uuid4().hex[:12]
    name = _safe_log_name(f"{scope}-{hook.name}-{timestamp}-{unique}")
    try:
        _cleanup_logs(root=root)
    except Exception:
        pass
    return codexer_root(root) / "logs" / f"{name}.log"


def _safe_log_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value)


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
    log_file = item.get("log_file")
    return Hook(
        str(item.get("name", "")),
        str(item.get("command", "")),
        profile,
        background=bool(item.get("background", False)),
        log_file=str(log_file) if log_file is not None else None,
    )


def _hook_to_item(hook: Hook) -> dict[str, object]:
    item: dict[str, object] = {"name": hook.name, "command": hook.command}
    if hook.background:
        item["background"] = True
    if hook.log_file is not None:
        item["log_file"] = hook.log_file
    return item


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


def _windows_minimum_env(env: Mapping[str, str]) -> dict[str, str]:
    full_env = dict(env)
    for name in ("SystemRoot", "WINDIR", "ComSpec"):
        if name not in full_env and name in os.environ:
            full_env[name] = os.environ[name]
    return full_env


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


def _symlink_target(source_path: Path, link_path: Path) -> str:
    if os.name == "nt":
        return str(source_path)
    # Prefer relative symlinks on POSIX so profiles remain movable with their source home.
    return os.path.relpath(str(source_path), start=str(link_path.parent))


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
