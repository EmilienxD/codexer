from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from codexer.core import (
    InvalidProfileName,
    ProfileExists,
    ProfileNotFound,
    add_profile,
    build_codex_env,
    list_profiles,
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


def test_add_profile_symlinks_files_and_skips_auth_and_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    result = add_profile("work", root=root, source_home=source)
    profile = root / "work"

    assert result.linked_files == 2
    assert result.skipped_files == (Path("auth.json"), Path("config.toml"))
    assert profile.is_dir()
    assert not (profile / "auth.json").exists()
    assert not (profile / "config.toml").exists()
    assert (profile / "instructions.md").is_symlink()
    assert (profile / "nested" / "tool.json").is_symlink()
    assert os.path.samefile(profile / "instructions.md", source / "instructions.md")


def test_add_profile_can_include_auth_and_config(tmp_path: Path) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)

    result = add_profile(
        "full",
        root=root,
        source_home=source,
        include_auth=True,
        include_config=True,
    )

    assert result.linked_files == 4
    assert result.skipped_files == ()
    assert (root / "full" / "auth.json").is_symlink()
    assert (root / "full" / "config.toml").is_symlink()


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

    (root / "work").mkdir(parents=True)
    built = build_codex_env("work", root=root, base_env=env)

    assert built["PATH"] == "x"
    assert built["CODEX_HOME"] == str(root / "work")


def test_run_codex_passes_args_and_profile_env(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = root / "work"
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

    code = run_codex(["--help"], executable="codex", base_env={"PATH": str(bin_dir)})

    assert code == 0
    assert "--help" in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b"])
def test_validate_profile_name_rejects_path_names(name: str) -> None:
    with pytest.raises(InvalidProfileName):
        validate_profile_name(name)
