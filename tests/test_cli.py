from __future__ import annotations

from pathlib import Path

import pytest

from codexer import cli


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
    assert "Skipped: auth.json, config.toml" in add_out

    assert cli.main(["list"]) == 0
    list_out = capsys.readouterr().out
    assert "demo" in list_out

    assert cli.main(["rm", "demo"]) == 0
    rm_out = capsys.readouterr().out
    assert "Removed profile 'demo'" in rm_out


def test_cli_rm_missing_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["rm", "missing"]) == 0

    assert "Warning: profile 'missing' does not exist" in capsys.readouterr().out


def test_cli_profile_dispatch_passes_remaining_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    (root / "demo").mkdir(parents=True)
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setenv("CODEXER_ROOT", str(root))
    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 7)

    assert cli.main(["demo", "--model", "gpt-5.4"]) == 7

    assert calls == [(["--model", "gpt-5.4"], "demo")]


def test_cli_flag_dispatch_aliases_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    monkeypatch.setattr(cli, "run_codex", lambda args, profile=None: calls.append((list(args), profile)) or 0)

    assert cli.main(["--help"]) == 0

    assert calls == [(["--help"], None)]


def test_cli_unknown_profile_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("CODEXER_ROOT", str(tmp_path / "profiles"))

    assert cli.main(["missing"]) == 2

    assert "Profile 'missing' does not exist" in capsys.readouterr().err
