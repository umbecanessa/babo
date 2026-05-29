"""File ledger index, path normalization, and wave ownership."""

from __future__ import annotations

from pathlib import Path

from nls.tools.agent_tools.file_ledger import FileLedger, normalize_ledger_path


def test_normalize_hybrid_windows_path():
    raw = (
        "packages/server/C:\\Users\\me\\AppData\\"
        "Roaming\\babo\\python-env\\Lib\\assemblyai\\x.py"
    )
    norm = normalize_ledger_path(raw)
    assert norm.replace("\\", "/").endswith("assemblyai/x.py") or "assemblyai" in norm


def test_normalize_json_blob_path():
    assert normalize_ledger_path('{"path": "packages/server/foo.py"') == (
        "packages/server/foo.py"
    )


def test_ledger_blocks_shared_path_for_delegate(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        3,
        {4: ["packages/server/services/transcription.py"]},
        shared_paths=["packages/server/services/__init__.py"],
    )
    err = ledger.check_mutation_allowed(
        "packages/server/services/__init__.py",
        {"role": "delegate", "delegate_index": 5, "wave": 3},
        file_exists=True,
    )
    assert err is not None
    assert "FILE LOCKED" in err


def test_ledger_allows_shared_path_when_co_owned(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    shared = "packages/server/services/__init__.py"
    ledger.set_wave_ownership(
        0,
        {
            0: ["packages/server/services/__init__.py"],
            1: ["packages/server/services/transcription.py"],
        },
        shared_paths=[shared],
    )
    ledger.record(
        shared,
        None,
        '"""pkg"""',
        "write",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
    )
    err = ledger.check_mutation_allowed(
        shared,
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=True,
    )
    assert err is None


def test_ledger_allows_co_owned_path_after_teammate_created(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    target = "backend/shared/types.py"
    ledger.set_wave_ownership(
        0,
        {
            0: ["backend/"],
            1: ["backend/"],
        },
    )
    ledger.record(
        target,
        None,
        "x",
        "write",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
    )
    err = ledger.check_mutation_allowed(
        target,
        {"role": "delegate", "delegate_index": 1, "wave": 0},
        file_exists=True,
    )
    assert err is None


def test_ledger_releases_delegate_scope(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        3,
        {
            4: ["backend/a.py"],
            5: ["backend/b.py"],
        },
    )
    ledger.record(
        "backend/a.py",
        None,
        "x",
        "write",
        {"role": "delegate", "delegate_index": 4, "wave": 3},
    )
    ledger.release_delegate_ownership(3, 4)
    err_released = ledger.check_mutation_allowed(
        "backend/a.py",
        {"role": "delegate", "delegate_index": 4, "wave": 3},
        file_exists=True,
    )
    assert err_released is not None
    assert "DELEGATE COMPLETE" in err_released
    err_teammate = ledger.check_mutation_allowed(
        "backend/a.py",
        {"role": "delegate", "delegate_index": 5, "wave": 3},
        file_exists=True,
    )
    assert err_teammate is None


def test_ledger_blocks_teammate_owned_file(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        3,
        {
            4: ["packages/server/services/transcription.py"],
            5: ["packages/server/services/icf_analysis.py"],
        },
    )
    ledger.record(
        "packages/server/services/transcription.py",
        None,
        '"""x"""',
        "write",
        {"role": "delegate", "delegate_index": 4, "wave": 3},
    )
    err = ledger.check_mutation_allowed(
        "packages/server/services/transcription.py",
        {"role": "delegate", "delegate_index": 5, "wave": 3},
        file_exists=True,
    )
    assert err is not None
    assert "TEAMMATE" in err


def test_ledger_matches_cwd_relative_paths_with_project_dir(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        0,
        {0: ["backend/", "README.md", ".gitignore"]},
        project_dir="my-app",
    )
    for path in (
        "backend/main.py",
        "my-app/backend/main.py",
        "README.md",
        "my-app/README.md",
    ):
        err = ledger.check_mutation_allowed(
            path,
            {"role": "delegate", "delegate_index": 0, "wave": 0},
            file_exists=False,
        )
        assert err is None, path
    err = ledger.check_mutation_allowed(
        "frontend/page.tsx",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=False,
    )
    assert err is None


def test_ledger_allows_scratch_files_outside_scope(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        0,
        {0: [".gitignore", "README.md", "backend/", "frontend/"]},
    )
    for path in ("tmp_body.json", ".tmp_body.json", "temp/payload.json"):
        err = ledger.check_mutation_allowed(
            path,
            {"role": "delegate", "delegate_index": 0, "wave": 0},
            file_exists=False,
        )
        assert err is None, path


def test_ledger_grant_delegate_paths_mid_wave(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        0,
        {0: ["backend/"]},
        shared_paths=[".gitignore"],
    )
    err_before = ledger.check_mutation_allowed(
        ".gitignore",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=False,
    )
    assert err_before is None
    granted = ledger.grant_delegate_paths(0, 0, [".gitignore"])
    assert ".gitignore" in granted
    err_existing = ledger.check_mutation_allowed(
        ".gitignore",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=True,
    )
    assert err_existing is None


def test_ledger_allows_shared_scaffold_create_when_missing(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        0,
        {0: ["backend/"]},
        shared_paths=["README.md", "package.json"],
    )
    for path in ("README.md", "package.json"):
        err = ledger.check_mutation_allowed(
            path,
            {"role": "delegate", "delegate_index": 0, "wave": 0},
            file_exists=False,
        )
        assert err is None, path
    err_locked = ledger.check_mutation_allowed(
        "README.md",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=True,
    )
    assert err_locked is not None
    assert "FILE LOCKED" in err_locked


def test_ledger_set_delegate_paths_replaces_scope(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(0, {0: ["backend/"]})
    ledger.set_delegate_paths(0, 0, ["frontend/", ".gitignore"])
    err = ledger.check_mutation_allowed(
        "frontend/app.tsx",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=False,
    )
    assert err is None
    err_git = ledger.check_mutation_allowed(
        ".gitignore",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=True,
    )
    assert err_git is None


def test_ledger_blocks_teammate_scope_even_for_new_file(tmp_path: Path):
    ledger = FileLedger(tmp_path / "file_ledger.jsonl")
    ledger.set_wave_ownership(
        0,
        {
            0: ["frontend/"],
            1: ["backend/"],
        },
    )
    err = ledger.check_mutation_allowed(
        "backend/main.py",
        {"role": "delegate", "delegate_index": 0, "wave": 0},
        file_exists=False,
    )
    assert err is not None
    assert "TEAMMATE" in err
