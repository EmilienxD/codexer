from __future__ import annotations

from pathlib import Path

import pytest

from codexer import cli
from codexer.core import profile_path


def make_source_home(path: Path) -> None:
    path.mkdir()
    (path / "auth.json").write_text("auth", encoding="utf-8")
    (path / "config.toml").write_text("config", encoding="utf-8")
    (path / "settings.json").write_text("settings", encoding="utf-8")


def test_cli_add_list_and_rm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("CODEXER_ROOT", str(root))

    assert cli.main(["add", "demo"]) == 0
    add_out = capsys.readouterr().out
    assert "Created profile 'demo'" in add_out
    assert "Skipped:" not in add_out
    assert "config.toml" not in add_out
    assert not (root / "demo" / "auth.json").is_symlink()
    assert not (root / "demo" / "config.toml").is_symlink()

    assert cli.main(["list"]) == 0
    list_out = capsys.readouterr().out
    assert "demo" in list_out

    assert cli.main(["rm", "demo"]) == 0
    rm_out = capsys.readouterr().out
    assert "Removed profile 'demo'" in rm_out


def test_cli_add_can_symlink_auth_and_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("CODEXER_ROOT", str(root))

    assert cli.main(["add", "demo", "--sym-auth", "--sym-config"]) == 0
    add_out = capsys.readouterr().out
    assert "Skipped:" not in add_out
    assert profile_path("demo", root=root).joinpath("auth.json").is_symlink()
    assert profile_path("demo", root=root).joinpath("config.toml").is_symlink()


def test_cli_command_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("CODEXER_ROOT", str(root))

    assert cli.main(["new", "demo"]) == 0
    assert cli.main(["ls"]) == 0
    assert "demo" in capsys.readouterr().out
    assert cli.main(["delete", "demo"]) == 0
    assert "Removed profile 'demo'" in capsys.readouterr().out


def test_cli_init_creates_profile_and_runs_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    root = tmp_path / "profiles"
    make_source_home(source)
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.setenv("CODEXER_ROOT", str(root))
    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["init", "demo", "--model", "gpt-5.4"]) == 0

    assert profile_path("demo", root=root).is_dir()
    assert calls == [(["--model", "gpt-5.4"], "demo")]
    assert "Created profile 'demo'" in capsys.readouterr().out


def test_cli_rm_missing_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["rm", "missing"]) == 0

    assert "Warning: profile 'missing' does not exist" in capsys.readouterr().out


def test_cli_run_profile_dispatch_passes_remaining_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile_path("demo", root=root).mkdir(parents=True)
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setenv("CODEXER_ROOT", str(root))
    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 7)

    assert cli.main(["run", "demo", "--model", "gpt-5.4"]) == 7

    assert calls == [(["--model", "gpt-5.4"], "demo")]


def test_cli_run_profile_strips_separator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile_path("demo", root=root).mkdir(parents=True)
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setenv("CODEXER_ROOT", str(root))
    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["run", "demo", "--", "hi"]) == 0

    assert calls == [(["hi"], "demo")]


def test_cli_flag_dispatch_aliases_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["--help"]) == 0

    assert calls == [(["--help"], None)]


def test_cli_bare_args_alias_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["hi"]) == 0

    assert calls == [(["hi"], None)]


def test_cli_separator_allows_reserved_word_codex_args(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["--", "add", "hello"]) == 0

    assert calls == [(["add", "hello"], None)]


def test_cli_run_missing_profile_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["run", "missing"]) == 2

    assert "Profile does not exist" in capsys.readouterr().err


def test_cli_hook_management_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["hook", "register", "prepare", "echo", "ready"]) == 0
    assert "Added hook 'prepare'" in capsys.readouterr().out

    assert cli.main(["hook", "ls"]) == 0
    list_out = capsys.readouterr().out
    assert "prepare" in list_out
    assert "echo ready" in list_out

    assert cli.main(["hook", "del", "prepare"]) == 0
    assert "Removed hook 'prepare'" in capsys.readouterr().out


def test_cli_hook_add_accepts_profile_after_quoted_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["hook", "add", "test", "echo hello", "--profile", "mytime"]) == 0
    assert "profile 'mytime': echo hello" in capsys.readouterr().out

    assert cli.main(["hook", "ls", "--profile", "mytime"]) == 0
    list_out = capsys.readouterr().out
    assert "mytime\ttest\techo hello" in list_out


def test_cli_hook_add_accepts_profile_equals_after_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["hook", "add", "test", "echo hello", "--profile=mytime"]) == 0
    capsys.readouterr()

    assert cli.main(["hook", "ls", "--profile", "mytime"]) == 0
    assert "mytime\ttest\techo hello" in capsys.readouterr().out
