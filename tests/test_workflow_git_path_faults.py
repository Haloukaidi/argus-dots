"""Isolated local Git/path workflow tests; Windows cases are simulations only.

Run with --basetemp in argus-runtime/full-workflows/git-paths. No remotes are
contacted: the only clone source is an owned fixture for the submodule test.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from argus.core import campaign_workdir, paths, project
from argus.core.portable_filename import portable_filename_component
from argus.skills.round_checkpoint import checkpoint_round


@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def make_repo(path: Path, *, commit: bool = True) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "checkout", "-q", "-b", "test-branch")
    git(path, "config", "user.name", "Fixture User")
    git(path, "config", "user.email", "fixture@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    if commit:
        (path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        git(path, "add", "--", "tracked.txt")
        git(path, "commit", "-q", "-m", "baseline")
    return path


def checkpoint(root: Path, mission: str = "fixture"):
    return checkpoint_round(root, mission_id=mission, round_index=1, message="fixture checkpoint")


def adopt(base: Path, target: Path, *, state: Path | None = None) -> Path:
    return campaign_workdir.adopt_campaign_workdir(
        state_root=state or base / "state", base_root=base, current_root=base,
        requested=str(target.relative_to(base)),
    )


def index_bytes(root: Path) -> bytes:
    index = Path(git(root, "rev-parse", "--git-path", "index").stdout.strip())
    return (index if index.is_absolute() else root / index).read_bytes()


@pytest.mark.parametrize("kind", ["plain", "unborn", "clean"])
def test_checkpoint_non_actionable_repository_is_noop(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "repo"
    if kind == "plain":
        root.mkdir()
    else:
        make_repo(root, commit=kind != "unborn")
    result = checkpoint(root)
    assert not result.recorded
    assert not result.error
    assert not result.ref


@pytest.mark.parametrize("change", ["dirty", "untracked", "staged", "deleted", "renamed"])
def test_checkpoint_preserves_branch_index_and_files(tmp_path: Path, change: str) -> None:
    root = make_repo(tmp_path / "研究 工作区")
    tracked = root / "tracked.txt"
    if change == "dirty":
        tracked.write_text("dirty\n", encoding="utf-8")
    elif change == "untracked":
        (root / "新建 file.txt").write_text("untracked\n", encoding="utf-8")
    elif change == "staged":
        tracked.write_text("staged\n", encoding="utf-8")
        git(root, "add", "--", "tracked.txt")
        tracked.write_text("unstaged after staging\n", encoding="utf-8")
    elif change == "deleted":
        tracked.unlink()
    else:
        tracked.rename(root / "重命名 file.txt")
    head_before = git(root, "rev-parse", "HEAD").stdout
    index_before = index_bytes(root)
    status_before = git(root, "status", "--porcelain=v1", "-z").stdout
    branch_before = git(root, "symbolic-ref", "HEAD").stdout
    files_before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    result = checkpoint(root)
    assert result.recorded, result.error
    assert git(root, "rev-parse", "HEAD").stdout == head_before
    assert git(root, "symbolic-ref", "HEAD").stdout == branch_before
    assert index_bytes(root) == index_before
    assert git(root, "status", "--porcelain=v1", "-z").stdout == status_before
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == files_before
    for name, contents in files_before.items():
        assert git(root, "show", f"{result.ref}:{name}").stdout.encode() == contents


@pytest.mark.parametrize("detached", [False, True])
def test_adoption_and_checkpoint_work_with_linked_worktree(tmp_path: Path, detached: bool) -> None:
    source = make_repo(tmp_path / "source")
    base = tmp_path / "base"
    base.mkdir()
    target = base / "linked 工作区"
    args = ["--detach"] if detached else ["-b", "linked-branch"]
    git(source, "worktree", "add", *args, str(target), "HEAD")
    assert (target / ".git").is_file()
    assert adopt(base, target) == target.resolve()
    assert campaign_workdir.active_campaign_workdir(base / "state", base) == target.resolve()
    source_index = index_bytes(source)
    target_index = index_bytes(target)
    target_head = git(target, "rev-parse", "HEAD").stdout
    (target / "linked.txt").write_text("change\n", encoding="utf-8")
    result = checkpoint(target)
    assert result.recorded, result.error
    assert index_bytes(source) == source_index
    assert index_bytes(target) == target_index
    assert git(target, "rev-parse", "HEAD").stdout == target_head
    assert not (source / "linked.txt").exists()


def test_submodule_adoption_and_checkpoint_preserve_parent(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "submodule-source")
    parent = make_repo(tmp_path / "parent")
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(source), "module 空格")
    git(parent, "commit", "-q", "-am", "local submodule fixture")
    target = parent / "module 空格"
    parent_index = index_bytes(parent)
    assert adopt(parent, target, state=tmp_path / "state") == target.resolve()
    (target / "new.txt").write_text("submodule change\n", encoding="utf-8")
    result = checkpoint(target)
    assert result.recorded, result.error
    assert git(target, "show", f"{result.ref}:new.txt").stdout == "submodule change\n"
    assert index_bytes(parent) == parent_index


def test_checkpoint_private_index_ignores_existing_real_index_lock(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    lock = root / ".git" / "index.lock"
    lock.write_text("other process owns this fixture lock", encoding="utf-8")
    index_before = index_bytes(root)
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    result = checkpoint(root)
    assert result.recorded, result.error
    assert index_bytes(root) == index_before
    assert lock.read_text() == "other process owns this fixture lock"


def test_checkpoint_ref_lock_reports_error_and_retry_preserves_head(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    head = git(root, "rev-parse", "HEAD").stdout
    lock = root / ".git/refs/argus/checkpoints/fixture/round-0001.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("held by fixture", encoding="utf-8")
    blocked = checkpoint(root)
    assert not blocked.recorded
    assert "lock" in blocked.error
    assert lock.read_text() == "held by fixture"
    lock.unlink()
    assert checkpoint(root).recorded
    assert git(root, "rev-parse", "HEAD").stdout == head


def test_independent_concurrent_checkpoints_keep_private_indexes(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / "new.txt").write_text("concurrent\n", encoding="utf-8")
    before = index_bytes(root)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda i: checkpoint(root, f"mission-{i}"), range(4)))
    assert all(result.recorded for result in results), results
    assert len({result.ref for result in results}) == 4
    assert index_bytes(root) == before


def test_git_unsafe_directory_is_not_adopted_or_added_to_safe_list(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base"
    target = make_repo(base / "repo")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    denied = git(target, "rev-parse", "--show-toplevel", check=False)
    assert "dubious ownership" in denied.stderr
    with pytest.raises(ValueError, match="real Git repository"):
        adopt(base, target)
    assert not (base / "state/campaign-workdir.json").exists()
    assert not Path(os.environ["GIT_CONFIG_GLOBAL"]).exists()


def test_checkpoint_unsafe_directory_has_explicit_failure(tmp_path: Path, monkeypatch) -> None:
    root = make_repo(tmp_path / "repo")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    result = checkpoint(root)
    assert not result.recorded
    assert "dubious ownership" in result.error


@pytest.mark.parametrize("fault", ["missing", "not-executable"])
def test_checkpoint_unavailable_git_returns_explicit_failure(tmp_path: Path, monkeypatch, fault: str) -> None:
    root = make_repo(tmp_path / "repo")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if fault == "not-executable":
        binary = bindir / "git"
        binary.write_text("fixture executable without permission", encoding="utf-8")
        binary.chmod(0o600)
    monkeypatch.setenv("PATH", str(bindir))
    result = checkpoint(root)
    assert not result.recorded
    assert result.error


def test_missing_git_fingerprint_falls_back_and_adoption_rejects(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base"
    target = make_repo(base / "repo")
    git(target, "remote", "add", "origin", "https://example.invalid/fixture.git")
    monkeypatch.setenv("PATH", str(tmp_path / "no-programs"))
    assert project.project_fingerprint(target).source == "cwd-path"
    with pytest.raises(ValueError, match="real Git repository"):
        adopt(base, target)


def test_checkpoint_unwritable_ref_is_explicit_and_non_destructive(tmp_path: Path) -> None:
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("requires a non-root POSIX permission boundary")
    root = make_repo(tmp_path / "repo")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    refs = root / ".git/refs"
    before = index_bytes(root)
    refs.chmod(0o500)
    try:
        result = checkpoint(root)
        assert not result.recorded
        assert result.error
        assert index_bytes(root) == before
    finally:
        refs.chmod(0o700)


def test_nonrepo_does_not_inherit_global_remote_identity(tmp_path: Path) -> None:
    first = tmp_path / "plain-one"
    second = tmp_path / "plain-two"
    first.mkdir()
    second.mkdir()
    # This config is under the test's isolated HOME; no user config is changed.
    Path(os.environ["GIT_CONFIG_GLOBAL"]).write_text(
        '[remote "origin"]\n\turl = https://example.invalid/global.git\n', encoding="utf-8",
    )
    a = project.project_fingerprint(first)
    b = project.project_fingerprint(second)
    assert a.source == b.source == "cwd-path"
    assert a.fingerprint != b.fingerprint


@pytest.mark.parametrize("mode", ["adopt", "checkpoint"])
def test_git_unicode_paths_use_utf8_under_simulated_windows_locale(tmp_path: Path, monkeypatch, mode: str) -> None:
    base = tmp_path / "base"
    target = make_repo(base / "研究 工作区")
    (target / "new.txt").write_text("new\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp936")
    if mode == "adopt":
        assert adopt(base, target) == target.resolve()
    else:
        result = checkpoint(target)
        assert result.recorded, result.error


def test_concurrent_campaign_adoptions_use_independent_atomic_writes(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "base"
    targets = [make_repo(base / name) for name in ("repo-a", "repo-b")]
    state = tmp_path / "state"
    both_written = threading.Barrier(2)
    original_replace = os.replace

    def simultaneous_replace(source, destination, *args, **kwargs):
        if Path(destination) == state / "campaign-workdir.json":
            both_written.wait(timeout=10)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(campaign_workdir.os, "replace", simultaneous_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(adopt, base, target, state=state) for target in targets]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=15))
            except Exception as exc:
                outcomes.append(exc)
    assert all(isinstance(value, Path) for value in outcomes), outcomes
    payload = json.loads((state / "campaign-workdir.json").read_text(encoding="utf-8"))
    assert Path(payload["workdir"]) in targets


@pytest.mark.parametrize("bad", ["../outside", "a/../../outside", "a\x00b"])
def test_campaign_invalid_relative_path_does_not_write_state(tmp_path: Path, bad: str) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        campaign_workdir.adopt_campaign_workdir(
            state_root=tmp_path / "state", base_root=base, current_root=base, requested=bad,
        )
    assert not (tmp_path / "state/campaign-workdir.json").exists()


@pytest.mark.parametrize("logical", ["x" * 120, "x" * 121, "中" * 40, "中" * 10000, "~" * 120, "a/b"])
@pytest.mark.parametrize("windows", [False, True], ids=["posix", "windows-simulation"])
def test_portable_identifiers_fit_real_component_limit(tmp_path: Path, logical: str, windows: bool) -> None:
    component = portable_filename_component(logical, windows=windows)
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX") if hasattr(os, "pathconf") else 255
    name = component + "_logs.json"
    assert len(os.fsencode(name)) <= name_max
    target = tmp_path / name
    target.write_text("fixture\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "fixture\n"


@pytest.mark.parametrize("logical", ["CON", "con.txt", "LPT1", "AUX.log", "trailing.", "trailing ", "x:y", "x?y", "x*y", "x\x00y"])
def test_windows_reserved_names_are_encoded_without_changing_safe_names(logical: str) -> None:
    assert portable_filename_component(logical, windows=True).startswith("~")
    assert portable_filename_component("正常 safe", windows=True) == "正常 safe"


def test_name_max_and_path_max_fail_without_creating_state(tmp_path: Path) -> None:
    if not hasattr(os, "pathconf"):
        pytest.skip("requires POSIX path limit discovery")
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    path_max = os.pathconf(tmp_path, "PC_PATH_MAX")
    for relative in ("x" * (name_max + 1), "/".join(["x" * 100] * (path_max // 100 + 1))):
        with pytest.raises(ValueError, match="not a directory"):
            campaign_workdir.resolve_task_workdir(tmp_path, relative)
    assert {p.name for p in tmp_path.iterdir()} == {"home", "temp"}


def test_component_name_max_failure_is_explicit_environment_limit(tmp_path: Path) -> None:
    if not hasattr(os, "pathconf"):
        pytest.skip("requires POSIX path limit discovery")
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    with pytest.raises(OSError) as caught:
        (tmp_path / ("x" * (name_max + 1))).mkdir()
    assert caught.value.errno == errno.ENAMETOOLONG


@pytest.mark.parametrize("value", ["../escape", "with/slash", "with\\slash", "bad\x00id"])
def test_session_path_rejects_traversal(value: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid session id"):
        paths.session_state_root(value, root=tmp_path)


def test_checkpoint_trailing_space_workdir_cannot_switch_repository(tmp_path: Path) -> None:
    intended = make_repo(tmp_path / "repo ")
    neighbor = make_repo(tmp_path / "repo")
    (intended / "intended.txt").write_text("intended change\n", encoding="utf-8")
    (neighbor / "neighbor.txt").write_text("unrelated change\n", encoding="utf-8")
    neighbor_refs = git(neighbor, "for-each-ref", "refs/argus/checkpoints").stdout
    result = checkpoint(intended)
    assert git(neighbor, "for-each-ref", "refs/argus/checkpoints").stdout == neighbor_refs
    assert result.recorded, result.error
    assert git(intended, "show", f"{result.ref}:intended.txt").stdout == "intended change\n"


@pytest.mark.parametrize("field", ["workdir", "base_workdir"])
def test_invalid_null_in_persisted_campaign_is_ignored(tmp_path: Path, field: str) -> None:
    base = tmp_path / "base"
    target = make_repo(base / "repo")
    state = tmp_path / "state"
    state.mkdir()
    data = {"base_workdir": str(base), "workdir": str(target)}
    data[field] += "\x00corrupt"
    (state / "campaign-workdir.json").write_text(json.dumps(data), encoding="utf-8")
    assert campaign_workdir.active_campaign_workdir(state, base) is None


def test_invalid_symlink_loop_in_persisted_campaign_is_ignored(tmp_path: Path, require_symlink_support) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "a").symlink_to("b")
    (base / "b").symlink_to("a")
    state = tmp_path / "state"
    state.mkdir()
    (state / "campaign-workdir.json").write_text(json.dumps({"workdir": str(base / "a")}), encoding="utf-8")
    assert campaign_workdir.active_campaign_workdir(state, base) is None


def test_real_deep_git_workflow_below_path_max(tmp_path: Path) -> None:
    if not hasattr(os, "pathconf"):
        pytest.skip("requires POSIX path limit discovery")
    path_max = os.pathconf(tmp_path, "PC_PATH_MAX")
    base = tmp_path / "deep"
    while len(os.fsencode(base)) + 81 < path_max - 600:
        base /= "d" * 80
    base.mkdir(parents=True)
    target = make_repo(base / "repo")
    (target / "deep-file.txt").write_text("deep content\n", encoding="utf-8")
    assert len(os.fsencode(target)) > path_max - 700
    result = checkpoint(target)
    assert result.recorded, result.error
    assert git(target, "show", f"{result.ref}:deep-file.txt").stdout == "deep content\n"
    assert adopt(base, target, state=tmp_path / "state") == target.resolve()


def test_real_existing_parents_exceed_path_max_explicitly(tmp_path: Path) -> None:
    if not hasattr(os, "pathconf"):
        pytest.skip("requires POSIX path limit discovery")
    path_max = os.pathconf(tmp_path, "PC_PATH_MAX")
    deepest = tmp_path / "deep"
    while len(os.fsencode(deepest)) + 81 < path_max - 25:
        deepest /= "d" * 80
    deepest.mkdir(parents=True)
    tail = "x" * (path_max - len(os.fsencode(deepest)))
    # Unlike an early missing directory, this crosses the real kernel limit
    # only after all parent directories have been created.
    with pytest.raises(ValueError, match="not a directory") as caught:
        campaign_workdir.resolve_task_workdir(deepest, tail)
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.ENAMETOOLONG


def test_git_filename_exact_name_max_roundtrips(tmp_path: Path) -> None:
    if not hasattr(os, "pathconf"):
        pytest.skip("requires POSIX path limit discovery")
    root = make_repo(tmp_path / "repo")
    filename = "f" * os.pathconf(root, "PC_NAME_MAX")
    (root / filename).write_text("name max\n", encoding="utf-8")
    result = checkpoint(root)
    assert result.recorded, result.error
    assert git(root, "show", f"{result.ref}:{filename}").stdout == "name max\n"


@pytest.mark.parametrize("location", ["root", "nested", "worktree"])
def test_real_worktree_remote_overrides_global_remote(tmp_path: Path, location: str) -> None:
    root = make_repo(tmp_path / "repo")
    git(root, "remote", "add", "origin", "https://example.invalid/local.git")
    Path(os.environ["GIT_CONFIG_GLOBAL"]).write_text(
        '[remote "origin"]\n\turl = https://example.invalid/global.git\n', encoding="utf-8",
    )
    if location == "nested":
        target = root / "nested"
        target.mkdir()
    elif location == "worktree":
        target = tmp_path / "linked"
        git(root, "worktree", "add", "--detach", str(target), "HEAD")
    else:
        target = root
    identity = project.project_fingerprint(target)
    assert identity.source == "git-remote"
    assert identity.label == "example.invalid/local"


def test_bare_repository_is_not_worktree_identity(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    git(root, "init", "--bare", "-q")
    git(root, "config", "remote.origin.url", "https://example.invalid/bare.git")
    assert project.project_fingerprint(root).source == "cwd-path"


@pytest.mark.parametrize("operation", ["write", "replace"])
def test_failed_adoption_retains_previous_record_and_cleans_temporary(tmp_path: Path, monkeypatch, operation: str) -> None:
    base = tmp_path / "base"
    first = make_repo(base / "first")
    second = make_repo(base / "second")
    state = tmp_path / "state"
    adopt(base, first, state=state)
    destination = state / "campaign-workdir.json"
    previous = destination.read_bytes()
    previous_mode = destination.stat().st_mode
    if operation == "replace":
        original = os.replace

        def fail_replace(source, target, *args, **kwargs):
            if Path(target) == destination:
                raise PermissionError(errno.EACCES, "fixture replacement denied")
            return original(source, target, *args, **kwargs)

        monkeypatch.setattr(campaign_workdir.os, "replace", fail_replace)
    else:
        original = Path.write_text

        def fail_write(path, data, *args, **kwargs):
            if path.is_relative_to(state) and path != destination:
                raise OSError(errno.ENOSPC, "fixture disk full")
            return original(path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(OSError):
        adopt(base, second, state=state)
    assert destination.read_bytes() == previous
    assert destination.stat().st_mode == previous_mode
    assert list(state.iterdir()) == [destination]
    assert campaign_workdir.active_campaign_workdir(state, base) == first.resolve()


def test_adoption_atomic_write_retains_original_create_mode(tmp_path: Path) -> None:
    base = tmp_path / "base"
    target = make_repo(base / "repo")
    control = tmp_path / "write-text-mode"
    control.write_text("fixture", encoding="utf-8")
    adopt(base, target)
    actual = base / "state/campaign-workdir.json"
    assert actual.stat().st_mode == control.stat().st_mode


@pytest.mark.parametrize("command", ["read-tree", "add", "write-tree", "commit-tree", "update-ref"])
def test_git_disappearing_mid_checkpoint_is_explicit_without_branch_mutation(tmp_path: Path, monkeypatch, command: str) -> None:
    root = make_repo(tmp_path / "repo")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    before_head = git(root, "rev-parse", "HEAD").stdout
    before_index = index_bytes(root)
    original = subprocess.run

    def fail_one_command(args, *positional, **kwargs):
        if args[:2] == ["git", command]:
            raise FileNotFoundError(errno.ENOENT, "fixture git disappeared")
        return original(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_one_command)
    result = checkpoint(root)
    assert not result.recorded
    assert result.error
    assert git(root, "rev-parse", "HEAD").stdout == before_head
    assert index_bytes(root) == before_index
    assert not list(Path(os.environ["TMPDIR"]).iterdir())


def test_checkpoint_broken_branch_reference_is_explicit_failure(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    (root / ".git/refs/heads/test-branch").write_text("broken ref\n", encoding="utf-8")
    result = checkpoint(root)
    assert not result.recorded
    assert result.error


@pytest.mark.parametrize("base_suffix", ["", " "])
def test_campaign_reopen_preserves_exact_trailing_space_paths(tmp_path: Path, require_symlink_support, base_suffix: str) -> None:
    base = tmp_path / ("base" + base_suffix)
    intended = make_repo(base / "repo ")
    neighbor = make_repo(base / "repo")
    alias = base / "alias"
    alias.symlink_to(intended, target_is_directory=True)
    state = tmp_path / "state"
    neighbor_head = git(neighbor, "rev-parse", "HEAD").stdout
    neighbor_refs = git(neighbor, "for-each-ref").stdout
    assert adopt(base, alias, state=state) == intended.resolve()
    assert campaign_workdir.active_campaign_workdir(state, base) == intended.resolve()
    assert git(neighbor, "rev-parse", "HEAD").stdout == neighbor_head
    assert git(neighbor, "for-each-ref").stdout == neighbor_refs


def test_localized_git_diagnostics_keep_nonrepo_noop(tmp_path: Path, monkeypatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("LC_ALL", "zh_CN.utf8")
    monkeypatch.setenv("LANGUAGE", "zh_CN")
    localized = git(plain, "rev-parse", "--show-toplevel", check=False)
    if "不是 git 仓库" not in localized.stderr:
        pytest.skip("installed Git has no Chinese diagnostic translations")
    result = checkpoint(plain)
    assert not result.recorded
    assert result.error == ""
    assert os.environ["LC_ALL"] == "zh_CN.utf8"
    assert os.environ["LANGUAGE"] == "zh_CN"


@pytest.mark.skipif(os.name != "posix", reason="POSIX byte paths only")
def test_non_utf8_git_workdir_roundtrips_without_neighbor_access(tmp_path: Path) -> None:
    root = make_repo(tmp_path / os.fsdecode(b"raw-\xff"))
    neighbor = make_repo(tmp_path / "raw-\ufffd")
    (root / "new.txt").write_text("raw path\n", encoding="utf-8")
    (neighbor / "other.txt").write_text("do not checkpoint\n", encoding="utf-8")
    neighbor_refs = git(neighbor, "for-each-ref", "refs/argus/checkpoints").stdout
    result = checkpoint(root)
    assert result.recorded, result.error
    assert git(neighbor, "for-each-ref", "refs/argus/checkpoints").stdout == neighbor_refs
    assert git(root, "show", f"{result.ref}:new.txt").stdout == "raw path\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX byte paths only")
def test_invalid_utf8_git_diagnostics_are_safe_to_report(tmp_path: Path) -> None:
    from argus.skills.round_checkpoint import _run

    missing = tmp_path / os.fsdecode(b"missing-\xff")
    result = _run(["-C", str(missing), "status"], tmp_path)
    assert result.returncode != 0
    assert result.stderr
    result.stderr.encode("utf-8")


@pytest.mark.skipif(os.name != "posix", reason="POSIX control characters in paths only")
@pytest.mark.parametrize("name,neighbor_name", [
    ("repo\t", "repo"),
    ("repo\n\n", "repo"),
    ("repo\r", "repo"),
    ("repo\n", "repo"),
    ("repo\r\n", "repo\n"),
    ("repo\rinside", "repo\ninside"),
])
def test_git_path_control_bytes_cannot_select_neighbor(tmp_path: Path, require_symlink_support, name: str, neighbor_name: str) -> None:
    base = tmp_path / "base"
    root = make_repo(base / name)
    neighbor = make_repo(base / neighbor_name)
    (root / "intended.txt").write_text("intended\n", encoding="utf-8")
    (neighbor / "neighbor.txt").write_text("neighbor\n", encoding="utf-8")
    neighbor_refs = git(neighbor, "for-each-ref").stdout
    neighbor_index = index_bytes(neighbor)
    result = checkpoint(root)
    assert result.recorded, result.error
    assert git(root, "show", f"{result.ref}:intended.txt").stdout == "intended\n"
    alias = base / "alias"
    alias.symlink_to(root, target_is_directory=True)
    state = tmp_path / "state"
    assert adopt(base, alias, state=state) == root.resolve()
    assert campaign_workdir.active_campaign_workdir(state, base) == root.resolve()
    assert git(neighbor, "for-each-ref").stdout == neighbor_refs
    assert index_bytes(neighbor) == neighbor_index


@pytest.mark.skipif(os.name != "posix", reason="POSIX byte paths only")
def test_campaign_non_utf8_path_persists_and_reopens_exactly(tmp_path: Path) -> None:
    base = tmp_path / "base"
    root = make_repo(base / os.fsdecode(b"raw-\xff"))
    neighbor = make_repo(base / "raw-\ufffd")
    state = tmp_path / "state"
    neighbor_refs = git(neighbor, "for-each-ref").stdout
    assert adopt(base, root, state=state) == root.resolve()
    assert campaign_workdir.active_campaign_workdir(state, base) == root.resolve()
    assert git(neighbor, "for-each-ref").stdout == neighbor_refs
    (state / "campaign-workdir.json").read_bytes().decode("utf-8")


def test_checkpoint_unborn_with_other_healthy_refs_stays_noop(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    git(root, "checkout", "--orphan", "new-branch")
    before = index_bytes(root)
    refs = git(root, "for-each-ref").stdout
    result = checkpoint(root)
    assert not result.recorded
    assert result.error == ""
    assert index_bytes(root) == before
    assert git(root, "for-each-ref").stdout == refs


def test_campaign_normal_unicode_persistence_remains_readable(tmp_path: Path) -> None:
    base = tmp_path / "base"
    root = make_repo(base / "研究 工作区")
    state = tmp_path / "state"
    assert adopt(base, root, state=state) == root.resolve()
    stored = (state / "campaign-workdir.json").read_text(encoding="utf-8")
    assert "研究 工作区" in stored
    assert campaign_workdir.active_campaign_workdir(state, base) == root.resolve()
