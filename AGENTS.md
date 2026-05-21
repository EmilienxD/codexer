# AGENTS.md

This project is `codexer`, a small Python CLI and SDK for managing multiple Codex CLI home directories on Windows first. It wraps the real `codex` executable, creates named Codex home profiles under `~/.codexer`, and can run configured shell hooks before launching Codex.

The repository is intentionally small. Do not add a README or extra documentation unless the user explicitly asks. Keep `uv` as the only dependency/source-of-truth workflow.

## Project Layout

- `pyproject.toml`
  - Python package metadata.
  - Defines the `codexer` console script as `codexer.cli:main`.
  - Uses `hatchling`.
  - Uses `dependency-groups.dev` for pytest.
  - No `requirements.txt`.
- `uv.lock`
  - Lockfile managed by `uv`.
- `src/codexer/core.py`
  - SDK and business logic.
  - Profile path resolution, profile creation/removal/listing, hook config storage, hook execution, and Codex process launching.
- `src/codexer/cli.py`
  - Thin human-facing CLI layer.
  - Parses commands, prints messages, delegates behavior to `core.py`.
- `src/codexer/__init__.py`
  - Re-exports the SDK surface from `core.py`.
- `tests/test_core.py`
  - Unit tests for SDK behavior, symlinks, env setup, hook storage, hook execution, and executable resolution.
- `tests/test_cli.py`
  - Unit tests for CLI dispatch, aliases, init, hook management, and regression coverage for hook `--profile` parsing.

## Runtime Model

`codexer` has two important directories:

- Source Codex home:
  - `CODEX_HOME` if set.
  - Otherwise `~/.codex`.
  - Used as the source when creating a new profile.
- Codexer root:
  - `CODEXER_ROOT` if set.
  - Otherwise `~/.codexer`.
  - Contains named profile folders and `hooks.json`.

Profile folders live at:

```text
~/.codexer/<profile-name>
```

Hook config lives at:

```text
~/.codexer/hooks.json
```

The default hook file shape is:

```json
{
  "version": 1,
  "profiles": {
    "*": [
      {
        "name": "prepare",
        "command": "echo ready"
      }
    ],
    "work": [
      {
        "name": "work-only",
        "command": "some command"
      }
    ]
  }
}
```

`"*"` means global hooks that run for every Codex launch through `codexer`. Profile-specific hooks are keyed by profile name.

## CLI Behavior

The main entry point is `codexer.cli:main`.

Supported commands:

- `codexer`
  - Alias to `codex`.
  - Runs `codex` with no extra args.
- `codexer <codex args starting with ->`
  - Alias to `codex`.
  - Forwards all args to `codex`.
- `codexer add <name> [--include-auth] [--exclude-config] [--open]`
- `codexer new <name> [--include-auth] [--exclude-config] [--open]`
- `codexer register <name> [--include-auth] [--exclude-config] [--open]`
  - Creates `~/.codexer/<name>`.
  - Recursively mirrors the current Codex home directory structure.
  - Creates symlinks for files.
  - Skips root-level `auth.json` unless `--include-auth` is passed.
  - Includes root-level `config.toml` unless `--exclude-config` is passed.
  - `--open` opens the created profile directory.
  - Errors if the profile already exists.
- `codexer init <name> [--include-auth] [--exclude-config] [--open] [codex args...]`
  - Same as `add`, then immediately runs `codex` with `CODEX_HOME` set to the new profile.
  - Errors if the profile already exists.
  - A leading `--` before Codex args is stripped if present.
- `codexer <name> [codex args...]`
  - Runs `codex` with `CODEX_HOME` set to `~/.codexer/<name>`.
  - Forwards remaining args to `codex`.
  - Errors if the profile does not exist.
- `codexer rm <name>`
- `codexer remove <name>`
- `codexer del <name>`
- `codexer delete <name>`
  - Removes the configured profile directory.
  - Prints a warning and returns success if the profile does not exist.
- `codexer list`
- `codexer ls`
  - Lists configured profile directories.
- `codexer hook add|new|register <name> <command> [--profile <profile>]`
- `codexer hook add|new|register <name> <command> [--profile=<profile>]`
  - Adds a hook command.
  - Default `--profile` is `*`, meaning all profiles.
  - The parser intentionally supports `--profile` after a quoted command, e.g.:
    ```powershell
    codexer hook add test "echo hello" --profile mytime
    ```
    This must store `command: "echo hello"` under profile `mytime`, not include `--profile mytime` in the command string.
- `codexer hook rm|remove|del|delete <name> [--profile <profile>]`
  - Removes a hook from a profile scope.
  - Defaults to global `*`.
  - Prints a warning and returns success if missing.
- `codexer hook list|ls [--profile <profile>]`
  - Lists all hooks or only hooks for one profile.

`hooks` is also accepted as an alias for `hook`.

## SDK Surface

The intended SDK lives in `codexer.core` and is re-exported from `codexer`.

Important functions:

- `codexer_root(root=None) -> Path`
- `codex_home(home=None) -> Path`
- `profile_path(name, root=None) -> Path`
- `validate_profile_name(name) -> str`
- `list_profiles(root=None) -> list[Profile]`
- `add_profile(name, include_auth=False, exclude_config=False, root=None, source_home=None) -> AddResult`
- `remove_profile(name, root=None) -> RemoveResult`
- `open_profile(name, root=None) -> Path`
- `build_codex_env(name, root=None, base_env=None) -> dict[str, str]`
- `run_codex(args, profile=None, root=None, executable="codex", base_env=None, run_configured_hooks=True) -> int`
- `hook_config_path(root=None) -> Path`
- `add_hook(name, command, profile="*", root=None) -> Hook`
- `remove_hook(name, profile="*", root=None) -> RemoveHookResult`
- `list_hooks(profile=None, root=None) -> list[Hook]`
- `run_hooks(profile, env, root=None) -> list[HookRunResult]`
- `validate_hook_name(name) -> str`

Important dataclasses:

- `Profile(name, path)`
- `AddResult(profile, source_home, linked_files, skipped_files)`
- `RemoveResult(name, path, removed)`
- `Hook(name, command, profile="*")`
- `RemoveHookResult(name, profile, removed)`
- `HookRunResult(hook, returncode)`

Important exceptions:

- `CodexerError`
- `InvalidProfileName`
- `ProfileExists`
- `ProfileNotFound`
- `SourceHomeMissing`
- `CodexExecutableNotFound`
- `HookExists`
- `HookFailed`

CLI code should stay thin and call the SDK functions. If adding behavior that automated scripts may need, add it to `core.py` first and have `cli.py` format human-readable messages.

## Implementation Details And Gotchas

- Windows is the primary target.
- File mirroring uses `os.walk` and `os.symlink` for files only.
- The profile directory tree is recreated with real directories.
- Root-level `auth.json` is skipped by default.
- Root-level `config.toml` is included by default and skipped only when config exclusion is requested.
- Skipped files are compared as relative `Path` values, e.g. `Path("auth.json")`.
- Profile names and hook names must not be empty, `.`, `..`, or contain `/` or `\`.
- `run_codex` uses `shutil.which(executable, path=env.get("PATH"))` before `subprocess.run`.
  - This is important on Windows so `codex.cmd` shims resolve correctly.
- `run_codex` sets `CODEX_HOME`:
  - To the selected profile path when `profile` is provided.
  - To `codex_home()` when no profile is provided and no `CODEX_HOME` exists in the env.
- Hooks run before Codex launches when `run_configured_hooks=True`.
- Hook commands run with `shell=True`.
  - This is intentional because hooks are user-configured shell snippets.
  - They receive the same environment that Codex will receive, including `CODEX_HOME`.
- Hook execution order:
  - Global hooks from profile `*`.
  - Then hooks for the selected profile, if any.
- If any hook exits non-zero, `run_hooks` raises `HookFailed` and Codex does not launch.
- `codexer hook add` uses a custom parser, not `argparse.REMAINDER`, because trailing `--profile` must remain a Codexer option even when the hook command is quoted.
- `_command_from_parts` preserves a single quoted command argument exactly. Multiple command parts are rejoined with Windows `subprocess.list2cmdline` on Windows and `shlex.join` elsewhere.

## Testing

Run the full test suite with:

```powershell
uv run pytest
```

Current expected state: all tests pass.

The suite covers:

- Profile creation with symlinks.
- Skipping and including `auth.json`; default inclusion and optional exclusion of `config.toml`.
- Existing-profile errors.
- Profile list and remove.
- `CODEX_HOME` environment construction.
- `codex.cmd` resolution through `PATH`.
- Hook add/list/remove.
- Hook execution with profile `CODEX_HOME`.
- CLI aliases.
- `init` creating a profile and then launching Codex.
- Hook command parsing where `--profile` appears after a quoted command.

When changing process launch, hook parsing, or Windows path behavior, add or update tests first. The Windows-specific tests are valuable and should not be weakened unless there is a replacement e2e check.

## Manual E2E Pattern

The project has no committed e2e script, but previous verification used this pattern:

1. Create a temp source Codex home under `C:\tmp`.
2. Create fake files:
   - `auth.json`
   - `config.toml`
   - another regular file
   - a nested file
3. Create a fake `codex.cmd` in a temp `bin` directory that writes `%CODEX_HOME%` and args to a file.
4. Optionally create a fake hook `.cmd` that writes `%CODEX_HOME%` to a file.
5. Set:
   - `CODEX_HOME=<temp source home>`
   - `CODEXER_ROOT=<temp profile root>`
   - `PATH=<temp bin>;<existing PATH>`
6. Run:
   - `uv run codexer hook register prepare <hook.cmd>`
   - `uv run codexer new demo`
   - `uv run codexer ls`
   - `uv run codexer demo --alpha beta`
   - `uv run codexer init fresh --gamma delta`
   - `uv run codexer hook ls`
   - `uv run codexer hook delete prepare`
   - `uv run codexer remove demo`
   - `uv run codexer del fresh`
7. Verify:
   - `auth.json` was skipped.
   - `config.toml` was linked unless config exclusion was requested.
   - Other files exist as symlinks.
   - Fake Codex saw the correct `CODEX_HOME` for each profile.
   - Hooks saw the same `CODEX_HOME` before Codex launched.

## Style And Maintenance Notes

- Keep the code dependency-free unless the user explicitly approves adding dependencies.
- Prefer plain dataclasses and functions over framework abstractions.
- Keep CLI output human-readable but stable enough for tests.
- Do not make destructive filesystem changes outside the configured profile root.
- Avoid changing the hook JSON format casually; if needed, keep backward compatibility with the existing `version: 1` shape.
- If adding more hook scopes or metadata, extend the `profiles` mapping instead of replacing it.
- If adding non-Windows support, preserve the current Windows behavior and tests.
