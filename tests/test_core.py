from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

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
