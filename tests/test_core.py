from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import codexer.core as core
from codexer.core import (
    profile_path,
    CodexerError,
    InvalidProfileName,
    ProfileExists,
    ProfileNotFound,
    add_hook,
    add_profile,
    build_codex_env,
    list_hooks,
    list_profiles,
    remove_hook,
    remove_profile,
    run_codex,
    validate_profile_name,
)


def make_source_home(path: Path) -> None:
    path.mkdir()
    (path / "auth.json").write_text("auth", encoding="utf-8")
    (path / "config.toml").write_text("config", encoding="utf-8")
    (path / "instructions.md").write_text("hello", encoding="utf-8")
    (path / "nested").mkdir()
    (path / "nested" / "tool.json").write_text("tool", encoding="utf-8")


def test_add_profile_copies_auth_and_config_by_default(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    result = add_profile("work", root=root, source_home=source)
    profile = profile_path("work", root=root)

    assert result.linked_files == 2
    assert result.skipped_files == ()
    assert profile.is_dir()
    assert (profile / "auth.json").read_text(encoding="utf-8") == "auth"
    assert not (profile / "auth.json").is_symlink()
    assert (profile / "config.toml").read_text(encoding="utf-8") == "config"
    assert not (profile / "config.toml").is_symlink()
    assert (profile / "instructions.md").is_symlink()
    assert (profile / "nested").is_symlink()
    (source / "auth.json").write_text("changed-auth", encoding="utf-8")
    (source / "config.toml").write_text("changed-config", encoding="utf-8")
    assert (profile / "auth.json").read_text(encoding="utf-8") == "auth"
    assert (profile / "config.toml").read_text(encoding="utf-8") == "config"
    (source / "nested" / "created-later.json").write_text("later", encoding="utf-8")
    assert (profile / "nested" / "created-later.json").read_text(encoding="utf-8") == "later"
    assert os.path.samefile(profile / "instructions.md", source / "instructions.md")


def test_add_profile_can_symlink_auth(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    result = add_profile(
        "full",
        root=root,
        source_home=source,
        sym_auth=True,
    )

    assert result.linked_files == 3
    assert result.skipped_files == ()
    assert (profile_path("full", root=root) / "auth.json").is_symlink()
    assert not (profile_path("full", root=root) / "config.toml").is_symlink()


def test_add_profile_can_symlink_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    result = add_profile("minimal", root=root, source_home=source, sym_config=True)

    assert result.linked_files == 3
    assert result.skipped_files == ()
    assert not (profile_path("minimal", root=root) / "auth.json").is_symlink()
    assert (profile_path("minimal", root=root) / "config.toml").is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="Relative symlink behavior is POSIX-specific.")
def test_add_profile_creates_relative_symlinks_on_posix(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    add_profile("work", root=root, source_home=source)
    link = profile_path("work", root=root) / "nested"

    target = link.readlink()
    assert not target.is_absolute()
    assert os.path.samefile(link, source / "nested")


def test_add_profile_rejects_existing_profile(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    add_profile("work", root=root, source_home=source)

    with pytest.raises(ProfileExists):
        add_profile("work", root=root, source_home=source)


def test_list_and_remove_profiles(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    add_profile("b", root=root, source_home=source)
    add_profile("a", root=root, source_home=source)

    assert [profile.name for profile in list_profiles(root=root)] == ["a", "b"]
    removed = remove_profile("a", root=root)
    missing = remove_profile("missing", root=root)

    assert removed.removed is True
    assert missing.removed is False
    assert [profile.name for profile in list_profiles(root=root)] == ["b"]


def test_build_codex_env_requires_existing_profile(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    env = {"PATH": "x"}

    with pytest.raises(ProfileNotFound):
        build_codex_env("missing", root=root, base_env=env)

    (profile_path("work", root=root)).mkdir(parents=True)
    built = build_codex_env("work", root=root, base_env=env)

    assert built["PATH"] == "x"
    assert built["CODEX_HOME"] == str(profile_path("work", root=root))


def test_run_codex_passes_args_and_profile_env(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    output = tmp_path / "out.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    profile.mkdir(parents=True)

    if sys.platform == "win32":
        executable = bin_dir / "codex.cmd"
        executable.write_text(
            f"@echo off\r\necho %CODEX_HOME% %* > {output}\r\n",
            encoding="utf-8",
        )
    else:
        executable = bin_dir / "codex"
        executable.write_text(
            f"#!/usr/bin/env sh\nprintf '%s %s\\n' \"$CODEX_HOME\" \"$*\" > '{output}'\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    code = run_codex(
        ["--version", "--flag=value"],
        profile="work",
        root=root,
        executable=str(executable),
        base_env={},
    )

    assert code == 0
    text = output.read_text(encoding="utf-8").strip()
    assert str(profile) in text
    assert "--version" in text
    assert "--flag=value" in text


def test_run_codex_resolves_executable_from_path(tmp_path: Path) -> None:
    output = tmp_path / "out.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    if sys.platform == "win32":
        executable = bin_dir / "codex.cmd"
        executable.write_text(f"@echo off\r\necho %* > {output}\r\n", encoding="utf-8")
    else:
        executable = bin_dir / "codex"
        executable.write_text(f"#!/usr/bin/env sh\nprintf '%s\\n' \"$*\" > '{output}'\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    code = run_codex(
        ["--help"],
        executable="codex",
        base_env={"PATH": str(bin_dir)},
        run_configured_hooks=False,
    )

    assert code == 0
    assert "--help" in output.read_text(encoding="utf-8")


def test_add_list_and_remove_hooks(tmp_path: Path) -> None:
    root = tmp_path / "profiles"

    hook = add_hook("prepare", "echo ready", root=root)
    scoped = add_hook("profile-only", "echo scoped", profile="work", root=root)

    assert hook.name == "prepare"
    assert hook.profile == "*"
    assert scoped.profile == "work"
    assert [(item.profile, item.name) for item in list_hooks(root=root)] == [
        ("*", "prepare"),
        ("work", "profile-only"),
    ]
    assert [item.name for item in list_hooks(profile="work", root=root)] == ["profile-only"]
    assert remove_hook("prepare", root=root).removed is True
    assert remove_hook("prepare", root=root).removed is False


def test_run_codex_runs_hooks_with_profile_env(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    hook_output = tmp_path / "hook.txt"
    codex_output = tmp_path / "codex.txt"

    if sys.platform == "win32":
        hook_script = tmp_path / "hook.cmd"
        hook_script.write_text(
            f"@echo off\r\necho HOOK=%CODEX_HOME% > \"{hook_output}\"\r\n",
            encoding="utf-8",
        )
        codex_script = tmp_path / "codex.cmd"
        codex_script.write_text(
            f"@echo off\r\necho CODEX=%CODEX_HOME% %* > \"{codex_output}\"\r\n",
            encoding="utf-8",
        )
        hook_command = f'"{hook_script}"'
    else:
        hook_script = tmp_path / "hook.sh"
        hook_script.write_text(
            f"#!/usr/bin/env sh\nprintf 'HOOK=%s\\n' \"$CODEX_HOME\" > '{hook_output}'\n",
            encoding="utf-8",
        )
        hook_script.chmod(hook_script.stat().st_mode | stat.S_IXUSR)
        codex_script = tmp_path / "codex"
        codex_script.write_text(
            f"#!/usr/bin/env sh\nprintf 'CODEX=%s %s\\n' \"$CODEX_HOME\" \"$*\" > '{codex_output}'\n",
            encoding="utf-8",
        )
        codex_script.chmod(codex_script.stat().st_mode | stat.S_IXUSR)
        hook_command = str(hook_script)

    add_hook("prepare", hook_command, root=root)

    code = run_codex(["--alpha"], profile="work", root=root, executable=str(codex_script), base_env={})

    assert code == 0
    assert f"HOOK={profile}" in hook_output.read_text(encoding="utf-8")
    assert f"CODEX={profile}" in codex_output.read_text(encoding="utf-8")


def test_run_codex_allows_hook_env_changes_to_reach_codex(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    codex_output = tmp_path / "codex.txt"

    if sys.platform == "win32":
        codex_script = tmp_path / "codex.cmd"
        codex_script.write_text(
            f"@echo off\r\necho FROM_HOOK=%FROM_HOOK% > \"{codex_output}\"\r\n",
            encoding="utf-8",
        )
        hook_command = "set FROM_HOOK=ready"
    else:
        codex_script = tmp_path / "codex"
        codex_script.write_text(
            f"#!/usr/bin/env sh\nprintf 'FROM_HOOK=%s\\n' \"$FROM_HOOK\" > '{codex_output}'\n",
            encoding="utf-8",
        )
        codex_script.chmod(codex_script.stat().st_mode | stat.S_IXUSR)
        hook_command = "export FROM_HOOK=ready"

    add_hook("env", hook_command, root=root)

    code = run_codex([], profile="work", root=root, executable=str(codex_script), base_env={})

    assert code == 0
    assert codex_output.read_text(encoding="utf-8").strip() == "FROM_HOOK=ready"


def test_run_codex_runs_foreground_hooks_before_background_hooks_and_codex(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    order_file = tmp_path / "order.txt"
    background_output = tmp_path / "background.txt"
    codex_output = tmp_path / "codex.txt"

    if sys.platform == "win32":
        foreground_command = f'set "CHAIN_VAR=ready" && echo foreground > "{order_file}"'
        background_script = tmp_path / "background.cmd"
        background_script.write_text(
            f"@echo off\r\n"
            f"echo background:%CHAIN_VAR% >> \"{order_file}\"\r\n"
            f"echo BACKGROUND=%CHAIN_VAR% > \"{background_output}\"\r\n",
            encoding="utf-8",
        )
        background_command = f'"{background_script}"'
        sleep_command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(.1)"])
        codex_script = tmp_path / "codex.cmd"
        codex_script.write_text(
            f"@echo off\r\n"
            f"for /l %%i in (1,1,50) do if not exist \"{background_output}\" {sleep_command}\r\n"
            f"echo codex:%CHAIN_VAR% >> \"{order_file}\"\r\n"
            f"echo CODEX=%CHAIN_VAR% > \"{codex_output}\"\r\n",
            encoding="utf-8",
        )
    else:
        foreground_command = f"export CHAIN_VAR=ready; printf 'foreground\\n' > {shlex.quote(str(order_file))}"
        background_script = tmp_path / "background.sh"
        background_script.write_text(
            f"#!/usr/bin/env sh\n"
            f"printf 'background:%s\\n' \"$CHAIN_VAR\" >> {shlex.quote(str(order_file))}\n"
            f"printf 'BACKGROUND=%s\\n' \"$CHAIN_VAR\" > {shlex.quote(str(background_output))}\n",
            encoding="utf-8",
        )
        background_script.chmod(background_script.stat().st_mode | stat.S_IXUSR)
        background_command = str(background_script)
        codex_script = tmp_path / "codex"
        codex_script.write_text(
            f"#!/usr/bin/env sh\n"
            f"i=0\n"
            f"while [ ! -f {shlex.quote(str(background_output))} ] && [ \"$i\" -lt 50 ]; do i=$((i + 1)); sleep 0.1; done\n"
            f"printf 'codex:%s\\n' \"$CHAIN_VAR\" >> {shlex.quote(str(order_file))}\n"
            f"printf 'CODEX=%s\\n' \"$CHAIN_VAR\" > {shlex.quote(str(codex_output))}\n",
            encoding="utf-8",
        )
        codex_script.chmod(codex_script.stat().st_mode | stat.S_IXUSR)

    add_hook("env", foreground_command, profile="work", root=root)
    add_hook("gateway", background_command, profile="work", background=True, root=root)

    code = run_codex([], profile="work", root=root, executable=str(codex_script), base_env={})

    assert code == 0
    assert background_output.read_text(encoding="utf-8").strip() == "BACKGROUND=ready"
    assert codex_output.read_text(encoding="utf-8").strip() == "CODEX=ready"
    assert [line.strip() for line in order_file.read_text(encoding="utf-8").splitlines()] == [
        "foreground",
        "background:ready",
        "codex:ready",
    ]


def test_run_codex_manages_background_hook_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    pid_file = tmp_path / "gateway.pid"
    codex_output = tmp_path / "codex.txt"
    log_file = tmp_path / "gateway.log"

    background_code = (
        "import os, pathlib, sys, time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "print('gateway ready'); sys.stdout.flush(); time.sleep(60)"
    )
    background_command = _shell_command([sys.executable, "-c", background_code])

    if sys.platform == "win32":
        codex_script = tmp_path / "codex.cmd"
        codex_script.write_text(
            f"@echo off\r\n"
            f"for /l %%i in (1,1,50) do if not exist \"{pid_file}\" powershell -NoProfile -Command \"Start-Sleep -Milliseconds 100\"\r\n"
            f"echo CODEX=%CODEX_HOME% > \"{codex_output}\"\r\n",
            encoding="utf-8",
        )
    else:
        codex_script = tmp_path / "codex"
        codex_script.write_text(
            f"#!/usr/bin/env sh\n"
            f"i=0\n"
            f"while [ ! -f '{pid_file}' ] && [ \"$i\" -lt 50 ]; do i=$((i + 1)); sleep 0.1; done\n"
            f"printf 'CODEX=%s\\n' \"$CODEX_HOME\" > '{codex_output}'\n",
            encoding="utf-8",
        )
        codex_script.chmod(codex_script.stat().st_mode | stat.S_IXUSR)

    add_hook(
        "gateway",
        background_command,
        profile="work",
        background=True,
        log_file=log_file,
        root=root,
    )

    code = run_codex([], profile="work", root=root, executable=str(codex_script), base_env={})

    assert code == 0
    text = codex_output.read_text(encoding="utf-8")
    assert f"CODEX={profile}" in text
    assert "gateway ready" in log_file.read_text(encoding="utf-8")

    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while time.time() < deadline and _process_is_running(pid):
        time.sleep(0.1)
    assert not _process_is_running(pid)


def test_background_hook_without_log_file_uses_unique_profile_logs(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    ready_file = tmp_path / "gateway.ready"

    background_code = (
        "import pathlib, sys, time; "
        "print('gateway ready'); sys.stdout.flush(); "
        f"pathlib.Path({str(ready_file)!r}).write_text('ready', encoding='utf-8'); "
        "time.sleep(60)"
    )
    background_command = _shell_command([sys.executable, "-c", background_code])

    if sys.platform == "win32":
        sleep_command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(.1)"])
        codex_script = tmp_path / "codex.cmd"
        codex_script.write_text(
            f"@echo off\r\n"
            f"for /l %%i in (1,1,50) do if not exist \"{ready_file}\" {sleep_command}\r\n",
            encoding="utf-8",
        )
    else:
        codex_script = tmp_path / "codex"
        codex_script.write_text(
            f"#!/usr/bin/env sh\n"
            f"i=0\n"
            f"while [ ! -f '{ready_file}' ] && [ \"$i\" -lt 50 ]; do i=$((i + 1)); sleep 0.1; done\n",
            encoding="utf-8",
        )
        codex_script.chmod(codex_script.stat().st_mode | stat.S_IXUSR)

    add_hook("gateway", background_command, profile="work", background=True, root=root)

    assert run_codex([], profile="work", root=root, executable=str(codex_script), base_env={}) == 0
    ready_file.unlink()
    assert run_codex([], profile="work", root=root, executable=str(codex_script), base_env={}) == 0

    logs = sorted((root / "logs").glob("work-gateway-*.log"))
    assert len(logs) == 2
    assert logs[0] != logs[1]
    assert all("gateway ready" in path.read_text(encoding="utf-8") for path in logs)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Ctrl+C behavior is platform-specific.")
def test_windows_foreground_background_plan_returns_130_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "profiles"
    profile = profile_path("work", root=root)
    profile.mkdir(parents=True)
    stopped: list[str] = []

    class FakeProcess:
        def poll(self):
            return 0

    def fake_run(args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        core,
        "_run_windows_foreground_hooks_for_env",
        lambda temp_path, foreground_hooks, env: dict(env, FROM_HOOK="ready"),
    )
    monkeypatch.setattr(
        core,
        "_start_background_hook",
        lambda hook, profile, env, root=None: core._BackgroundHookRun(hook, FakeProcess(), object()),
    )
    monkeypatch.setattr(core, "_stop_background_hook", lambda background: stopped.append(background.hook.name))
    monkeypatch.setattr(subprocess, "run", fake_run)

    add_hook("env", "set FROM_HOOK=ready", profile="work", root=root)
    add_hook("gateway", "gateway.cmd", profile="work", background=True, root=root)

    assert run_codex([], profile="work", root=root, executable="codex", base_env={}) == 130
    assert stopped == ["gateway"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX opener behavior is non-Windows.")
def test_open_profile_errors_when_opener_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from codexer.core import open_profile

    root = tmp_path / "profiles"
    (profile_path("work", root=root)).mkdir(parents=True)
    monkeypatch.setenv("CODEXER_ROOT", str(root))

    def fake_popen(args, **kwargs):
        raise FileNotFoundError(str(args[0]))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(CodexerError, match="opener"):
        open_profile("work", root=root)


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_validate_profile_name_rejects_path_names(name: str) -> None:
    with pytest.raises(InvalidProfileName):
        validate_profile_name(name)


def _shell_command(args: list[str]) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _process_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cleanup_logs_basic(tmp_path: Path) -> None:
    from codexer.core import _cleanup_logs

    root = tmp_path
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)

    log1 = logs_dir / "test1.log"
    log2 = logs_dir / "test2.log"
    log3 = logs_dir / "test3.log"

    log1.write_text("a" * 10, encoding="utf-8")
    log2.write_text("b" * 20, encoding="utf-8")
    log3.write_text("c" * 30, encoding="utf-8")

    import time
    now = time.time()
    os.utime(log1, (now - 100, now - 100))
    os.utime(log2, (now - 50, now - 50))
    os.utime(log3, (now, now))

    _cleanup_logs(root=root, max_bytes=35)

    assert not log1.exists()
    assert not log2.exists()
    assert log3.exists()


def test_cleanup_logs_with_open_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from codexer.core import _cleanup_logs

    root = tmp_path
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)

    log1 = logs_dir / "test1.log"
    log2 = logs_dir / "test2.log"
    log3 = logs_dir / "test3.log"

    log1.write_text("a" * 10, encoding="utf-8")
    log2.write_text("b" * 20, encoding="utf-8")
    log3.write_text("c" * 30, encoding="utf-8")

    import time
    now = time.time()
    os.utime(log1, (now - 100, now - 100))
    os.utime(log2, (now - 50, now - 50))
    os.utime(log3, (now, now))

    class FakeOpenFile:
        def __init__(self, path: str):
            self.path = path

    class FakeProcess:
        def __init__(self):
            self.info = {"open_files": [FakeOpenFile(str(log1.resolve()))]}

    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: [FakeProcess()])

    _cleanup_logs(root=root, max_bytes=35)

    assert log1.exists()
    assert not log2.exists()
    assert not log3.exists()


def test_cleanup_logs_with_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from codexer.core import _cleanup_logs

    root = tmp_path
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)

    log1 = logs_dir / "test1.log"
    log2 = logs_dir / "test2.log"

    log1.write_text("a" * 10, encoding="utf-8")
    log2.write_text("b" * 20, encoding="utf-8")

    import time
    now = time.time()
    os.utime(log1, (now - 100, now - 100))
    os.utime(log2, (now, now))

    orig_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if self.resolve() == log1.resolve():
            raise PermissionError("Access denied")
        return orig_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    _cleanup_logs(root=root, max_bytes=15)

    assert log1.exists()
    assert not log2.exists()

