"""A local path locates content; it is not a hash verdict or an access grant."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus.core.model_visible_text import (
    sanitize_model_visible_text,
    sanitize_reviewer_account,
)

HEX = "a1b2c3d4" * 4


def output(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Current artifact content.\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "name,wrapper,absolute",
    [
        (HEX + ".json", "{}", False),
        (HEX + "/result.json", "{}", False),
        (HEX + ".json", "`{}`", False),
        (HEX + ".json", '"{}"', False),
        ("中文 空格/" + HEX + ".json", "`{}`", False),
        ("中文 空格/" + HEX + ".json", '"{}"', True),
        (HEX + ".json", "{}", True),
        ("result-" + HEX + ".json", "'{}'", False),
    ],
)
def test_existing_local_path_keeps_exact_locator(tmp_path, name, wrapper, absolute):
    path = output(tmp_path, name)
    token = wrapper.format(str(path) if absolute else name)
    text = f"Inspect {token}\nThe implementation still needs independent review."
    assert sanitize_reviewer_account(text, working_dir=tmp_path) == text
    assert HEX not in sanitize_model_visible_text(text), "General sanitizer is unchanged"


def test_path_assignment_keeps_only_locator_not_unrelated_digest(tmp_path):
    output(tmp_path, HEX + ".json")
    text = f"ARTIFACT_PATH={HEX}.json\nSHA256={HEX}\nEverything else."
    result = sanitize_reviewer_account(text, working_dir=tmp_path)
    assert f"ARTIFACT_PATH={HEX}.json" in result
    assert f"SHA256={HEX}" not in result
    assert result.count(HEX) == 1
    assert result.endswith("Everything else.")


@pytest.mark.parametrize("token", [HEX, "`" + HEX + "`", '"' + HEX + '"', "sha256=" + HEX])
def test_pure_identifier_never_becomes_locator_even_if_named_file_exists(tmp_path, token):
    output(tmp_path, HEX)
    assert HEX not in sanitize_reviewer_account(token, working_dir=tmp_path)


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "outside", "parent", "relative-root", "no-root"]
)
def test_unrecognized_or_outside_locator_stays_redacted(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    name = HEX + ".json"
    token = name
    working_dir = root
    if kind == "directory":
        (root / name).mkdir()
    elif kind == "outside":
        token = str(output(tmp_path, name))
    elif kind == "parent":
        output(tmp_path, name)
        token = "../" + name
    elif kind == "relative-root":
        output(root, name)
        working_dir = Path("project")
    elif kind == "no-root":
        output(root, name)
        working_dir = None
    assert HEX not in sanitize_reviewer_account(f"Inspect `{token}`", working_dir=working_dir)


@pytest.mark.parametrize("kind", ["file", "directory", "root", "root-ancestor"])
def test_links_are_not_protected(tmp_path, require_symlink_support, kind):
    real = tmp_path / "real"
    actual = output(real, HEX + ".json")
    root = tmp_path / "project"
    root.mkdir()
    if kind == "file":
        token = HEX + ".json"
        (root / token).symlink_to(actual)
    elif kind == "directory":
        (root / "linked").symlink_to(real, target_is_directory=True)
        token = "linked/" + HEX + ".json"
    elif kind == "root":
        root = tmp_path / "alias"
        root.symlink_to(real, target_is_directory=True)
        token = HEX + ".json"
    else:
        output(real, "child/" + HEX + ".json")
        (tmp_path / "alias").symlink_to(real, target_is_directory=True)
        root = tmp_path / "alias" / "child"
        token = HEX + ".json"
    assert HEX not in sanitize_reviewer_account(f"Inspect `{token}`", working_dir=root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="Requires a real FIFO")
def test_nonregular_file_is_not_opened_or_protected(tmp_path):
    path = tmp_path / (HEX + ".json")
    os.mkfifo(path)
    assert HEX not in sanitize_reviewer_account(f"Inspect {path}", working_dir=tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "https://example.invalid/?file=" + HEX + ".json",
        "https://example.invalid/[" + HEX + ".json]",
        "prefix`" + HEX + ".json`suffix",
        '\\"' + HEX + '.json\\"',
        "SHA256=" + HEX + ".json",
    ],
)
def test_locator_substrings_are_not_recovered_from_other_syntax(tmp_path, text):
    output(tmp_path, HEX + ".json")
    assert HEX not in sanitize_reviewer_account(text, working_dir=tmp_path)


@pytest.mark.parametrize("kind", ["known", "pattern", "assignment"])
def test_credential_scrubbing_cannot_be_undone_by_real_file(tmp_path, monkeypatch, kind):
    if kind == "known":
        monkeypatch.setenv("FIXTURE_API_KEY", HEX)
        path = output(tmp_path, HEX + ".json")
        text = f"Inspect `{path}`"
        secret = HEX
    elif kind == "pattern":
        secret = "ghp_" + "a" * 30
        path = output(tmp_path, secret + "-" + HEX + ".json")
        text = f"Inspect `{path}`"
    else:
        path = output(tmp_path, HEX + ".json")
        text = f"api_key={path}"
        secret = HEX
    result = sanitize_reviewer_account(text, working_dir=tmp_path)
    assert secret not in result
    assert "<REDACTED:" in result


def test_candidate_budget_sanitizes_remainder_without_dropping_prose(tmp_path):
    paths = [output(tmp_path, f"{index:032x}.json").name for index in range(66)]
    text = "\n".join(paths) + "\nNORMAL TAIL MUST REMAIN"
    result = sanitize_reviewer_account(text, working_dir=tmp_path)
    assert paths[0] in result and paths[63] in result
    assert paths[64] not in result and paths[65] not in result
    assert result.endswith("NORMAL TAIL MUST REMAIN")
    assert len(result.splitlines()) == len(text.splitlines())


def test_oversized_token_is_not_partially_recovered(tmp_path):
    output(tmp_path, HEX + ".json")
    text = "`" + ("x/" * 2100) + HEX + ".json`\nNORMAL TAIL"
    result = sanitize_reviewer_account(text, working_dir=tmp_path)
    assert HEX not in result and result.endswith("NORMAL TAIL")
    assert "x/" * 2100 in result


@pytest.mark.parametrize("suffix", ["/", "/.", "\\", "\\."])
def test_invalid_trailing_path_syntax_is_not_repaired(tmp_path, suffix):
    path = output(tmp_path, HEX + ".json")
    text = f"Inspect `{path}{suffix}`"
    assert HEX not in sanitize_reviewer_account(text, working_dir=tmp_path)


def test_ordinary_prose_and_same_file_changes_are_not_cached(tmp_path):
    path = output(tmp_path, HEX + ".json")
    text = f"Inspect `{path}`; judge its contents, not its name."
    assert sanitize_reviewer_account(text, working_dir=tmp_path) == text
    path.unlink()
    assert HEX not in sanitize_reviewer_account(text, working_dir=tmp_path)
    path.write_text("New content after publication.\n")
    assert sanitize_reviewer_account(text, working_dir=tmp_path) == text
    prose = "No file was generated. The task is still incomplete."
    assert sanitize_reviewer_account(prose, working_dir=tmp_path) == prose
