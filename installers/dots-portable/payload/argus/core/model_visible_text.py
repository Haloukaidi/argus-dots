"""Keep opaque machine identifiers out of model-facing semantic judgment.

Checksums and content digests are useful to host code for cache keys, atomic
identity, corruption detection, and deduplication.  Their values are not useful
semantic evidence for an LLM: comparing two opaque strings cannot establish
correctness, freshness, provenance, or task completion.

This module therefore owns the boundary between machine-only integrity metadata
and text shown to or produced by a role agent.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

MODEL_INTEGRITY_BOUNDARY = """## Opaque integrity IDs
Checksums, digests, fingerprints, and commit IDs are host-only. Never inspect,
quote, compare, or use their values as evidence. Differences cannot prove
freshness, correctness, provenance, completion, contradiction, or justify
`continue`, `blocked`, or `replan_requested`. Use content, timestamps, tests,
metrics, and readable provenance; ignore lower-level identifier adjudication.
"""

_HEX_VALUE = r"[0-9a-f]{7,128}"
_PREFIXED_DIGEST_RE = re.compile(rf"(?i)\b(?:sha(?:-?1|-?256|-?512)?|md5):{_HEX_VALUE}\b")
_LABELED_IDENTIFIER_RE = re.compile(
    rf"""(?ix)
    \b[a-z0-9_.-]*
    (?:sha(?:-?256)?|hash|checksum|digest|fingerprint|commit(?:[_ -]?id)?|revision)
    [a-z0-9_.-]*\b
    \s*(?::|=|\bis\b)?\s*
    [`\"']?(?:sha(?:-?256)?:)?{_HEX_VALUE}[`\"']?
    """
)
_BARE_LONG_HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32,128}(?![0-9a-f])")
_INTEGRITY_TERM_RE = re.compile(
    r"(?i)\b(?:sha(?:-?256)?|hash(?:es|ed|ing)?|checksum|digest|fingerprint)\b"
    r"|哈希|校验和|摘要值"
)
_INTEGRITY_JUDGMENT_RE = re.compile(
    r"(?i)\b(?:match(?:es|ed|ing)?|mismatch(?:es|ed)?|stale|fresh|same|different|"
    r"changed?|drift(?:ed|ing)?|contradict(?:s|ed|ory)?|invalid|missing|verify|"
    r"verified|passes?|passed|fails?|failed|refresh(?:ed)?)\b"
    r"|一致|不一致|陈旧|过期|匹配|不同|变化|漂移|校验|验证|冲突|失效"
)
_MATERIAL_BLOCKER_RE = re.compile(
    r"(?i)\b(?:incomplete|missing|required|must|need(?:s|ed)?|fix|repair|reject|"
    r"fail(?:s|ed|ure)?|wrong|unsatisfied|unresolved|cannot|can't|blocker|"
    r"problem|issue|gap|lacks?|unable)\b"
    r"|\b(?:could\s+not|was\s+not\s+able)\b"
    r"|not\s+(?:done|complete|completed|satisfied|verified)"
    r"|does\s+not\s+(?:meet|pass)"
    r"|未完成|缺失|必须|需要|修复|失败|未满足|阻塞|问题"
)


def sanitize_model_visible_text(value: object) -> str:
    """Redact opaque integrity values before text reaches a role model."""
    text = str(value or "")
    text = _LABELED_IDENTIFIER_RE.sub("<machine-integrity-metadata omitted>", text)
    text = _PREFIXED_DIGEST_RE.sub("<machine-integrity-metadata omitted>", text)
    return _BARE_LONG_HEX_RE.sub("<machine-integrity-metadata omitted>", text)


_LOCAL_FILE_TOKEN_RE = re.compile(
    r"`(?P<backtick>[^`\r\n]+)`"
    r'|"(?P<double>[^"\r\n]+)"'
    r"|'(?P<single>[^'\r\n]+)'"
    r"|(?P<bare>[^\s`\"'=,;()\[\]{}<>]+)"
)
_MAX_LOCAL_FILE_CANDIDATES = 64
_MAX_LOCAL_FILE_TOKEN_CHARS = 4096
_LOCAL_FILE_ASSIGNMENT_RE = re.compile(r"(?i)(?:^|\s)(?:artifact_path|path|file|output)=$")


def _is_local_path_of_kind(token: str, root: Path, *, directory: bool = False) -> bool:
    """Recognize a locator, not grant permission to read it or certify its bytes."""
    if (
        len(token) > _MAX_LOCAL_FILE_TOKEN_CHARS
        or any(ord(char) < 32 or ord(char) == 127 for char in token)
        or "://" in token
        or token.startswith("~")
        or token.endswith(("/", "\\", "/.", "\\."))
        # An opaque ID by itself is never a file locator, even if a file with
        # that name exists. Use ./name for an extensionless relative filename.
        or not any(char in token for char in ("/", "\\", "."))
    ):
        return False
    try:
        candidate = Path(token)
        if ".." in candidate.parts:
            return False
        candidate = candidate if candidate.is_absolute() else root / candidate
        relative = candidate.relative_to(root)
        current = Path(root.anchor)
        for part in ("", *root.parts[1:], *relative.parts):
            if part:
                current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                return False
        return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    except (OSError, RuntimeError, ValueError):
        return False


def sanitize_reviewer_account(value: object, *, working_dir: str | Path | None) -> str:
    """Keep exact, existing local file locators usable in Engineer's account.

    The ordinary integrity sanitizer stays unchanged for every other caller.
    Only path tokens already present after credential redaction are considered;
    no directory scanning, path repair, content read, or extra root is allowed.
    Files outside the explicit workdir, missing files, and links retain ordinary
    redaction. Existence is a rendering hint, not a durable publication receipt:
    Reviewer still needs its real read and native action, under its sandbox.
    """
    from .secret_guard import known_secret_values, redact_secrets_text

    secrets = known_secret_values()
    text = redact_secrets_text(str(value or ""), known_values=secrets)
    if working_dir is None:
        return sanitize_model_visible_text(text)
    root = Path(working_dir)
    if not root.is_absolute() or ".." in root.parts or root.anchor.startswith("\\\\"):
        return sanitize_model_visible_text(text)
    pieces: list[str] = []
    cursor = candidates = 0
    for match in _LOCAL_FILE_TOKEN_RE.finditer(text):
        # Exact standalone tokens or an explicit file/path assignment only;
        # never preserve a substring of a URL query, escaped string, or word.
        if match.start() and not text[match.start() - 1].isspace():
            if not _LOCAL_FILE_ASSIGNMENT_RE.search(text[max(0, match.start() - 32):match.start()]):
                continue
        if match.end() < len(text) and not text[match.end()].isspace():
            if text[match.end()] not in ",;.!?:)]}":
                continue
        token = next(group for group in match.groups() if group is not None)
        if sanitize_model_visible_text(token) == token:
            continue
        if candidates >= _MAX_LOCAL_FILE_CANDIDATES:
            break
        candidates += 1
        if not _is_local_path_of_kind(token, root):
            continue
        pieces.append(sanitize_model_visible_text(text[cursor:match.start()]))
        pieces.append(match.group())
        cursor = match.end()
    pieces.append(sanitize_model_visible_text(text[cursor:]))
    # Never restore raw pre-redaction bytes, including credentials embedded in
    # an otherwise real filename. Bounds only limit recognition, not prose.
    return redact_secrets_text("".join(pieces), known_values=secrets)


def sanitize_reviewer_host_path(
    path: Path,
    *,
    allowed_roots: tuple[str | Path | None, ...],
    directory: bool = False,
    resolve_display: bool = False,
) -> str:
    """Render one host-supplied locator inside independently configured roots.

    This is not a parser for model text. Directory recognition is opt-in only
    for the fixed source-cache pointer; Engineer accounts remain file-only.
    Relative, missing, linked, wrong-kind or out-of-root paths retain ordinary
    integrity redaction. No root is ever inferred from the path being rendered.
    """
    from .secret_guard import known_secret_values, redact_secrets_text

    secrets = known_secret_values()
    original = str(path)
    redacted = redact_secrets_text(original, known_values=secrets)
    if redacted != original:
        return sanitize_model_visible_text(redacted)
    if path.is_absolute():
        for configured_root in allowed_roots:
            if configured_root is None:
                continue
            root = Path(configured_root)
            if not root.is_absolute() or ".." in root.parts or root.anchor.startswith("\\\\"):
                continue
            if _is_local_path_of_kind(original, root, directory=directory):
                # Preserve the validated lexical locator, never a resolved
                # target which could change during the filesystem observation.
                return original
    # Preserve the source-cache caller's old resolved display only on the
    # ordinary-redaction path, where it receives no locator exception.
    display = str(path.resolve()) if resolve_display else original
    return sanitize_model_visible_text(redact_secrets_text(display, known_values=secrets))


def contains_integrity_judgment(value: object) -> bool:
    """Return whether text asks a semantic verdict from opaque identifiers."""
    text = str(value or "")
    return bool(_INTEGRITY_TERM_RE.search(text) and _INTEGRITY_JUDGMENT_RE.search(text))


def sanitize_model_judgment_text(value: object) -> str:
    """Remove identifier-based verdict clauses and redact remaining values."""
    text = str(value or "").strip()
    if not text:
        return ""
    units = re.split(r"(?<=[.!?。！？;；])\s+|\n+", text)
    kept: list[str] = []
    for unit in units:
        cleaned = unit.strip()
        if not cleaned:
            continue
        if _INTEGRITY_TERM_RE.search(cleaned) and _INTEGRITY_JUDGMENT_RE.search(cleaned):
            continue
        kept.append(sanitize_model_visible_text(cleaned))
    return " ".join(kept).strip()


def has_material_blocker(value: object) -> bool:
    """Return whether sanitized prose still names a non-integrity blocker."""
    return bool(_MATERIAL_BLOCKER_RE.search(str(value or "")))


__all__ = [
    "MODEL_INTEGRITY_BOUNDARY",
    "contains_integrity_judgment",
    "has_material_blocker",
    "sanitize_model_judgment_text",
    "sanitize_model_visible_text",
    "sanitize_reviewer_account",
    "sanitize_reviewer_host_path",
]
