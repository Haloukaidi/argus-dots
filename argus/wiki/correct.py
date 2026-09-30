"""Correct a knowledge page where its readers look first, keeping what it said.

A page that turned out to be wrong is fixed in the two places a role or a
person reads before anything else: the summary in its front matter and the
paragraph that opens its body. The previous wording moves to a ``## History``
section at the end of the page, which recall does not search, and the file as
it was is kept under the library's ``.history`` directory, which browsing and
recall skip. The front matter records when and by whom the page was corrected,
so recall can say so in the line it shows for the page, and the knowledge
journal records the correction so the feed shows it.

Layer: capabilities
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .journal import append_knowledge_event
from .store import _atomic_write_text

HISTORY_DIRNAME = ".history"
HISTORY_HEADING = "## History"
_FRONT_MATTER_CHARS = 8_000
_TEXT_LIMIT = 4_000


@dataclass(frozen=True)
class PageCorrection:
    """What a correction did to one page."""

    page: Path
    relative: str
    title: str
    page_kind: str
    source: str
    corrected: str
    previous_description: str
    description: str
    previous_lead: str
    statement: str
    history_copy: Path
    index_updated: bool


class PageCorrectionError(ValueError):
    """The page cannot be corrected as asked; the message says why."""


def _utc(now: float | None) -> datetime:
    return datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)


def _one_paragraph(value: str, *, name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise PageCorrectionError(f"a correction needs a non-empty {name}")
    if len(text) > _TEXT_LIMIT:
        raise PageCorrectionError(f"the {name} is longer than {_TEXT_LIMIT} characters")
    return text


def _resolve_page(library: Path, page: str | Path) -> tuple[Path, str]:
    """The page file and its path relative to the library (``pages/...``)."""
    pages_root = (library / "pages").resolve()
    candidate = Path(page)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        parts = candidate.parts
        if parts and parts[0] == "pages":
            candidate = Path(*parts[1:]) if len(parts) > 1 else Path()
        target = (pages_root / candidate).resolve()
    if not target.is_relative_to(pages_root) or target == pages_root:
        raise PageCorrectionError("the page must sit under the library's pages/ directory")
    relative = target.relative_to(pages_root)
    if target.suffix.casefold() != ".md":
        raise PageCorrectionError("a knowledge page ends in .md")
    if any(part.startswith(".") or part == "_retired" for part in relative.parts):
        raise PageCorrectionError("hidden and retired pages are not corrected in place")
    if not target.is_file():
        raise PageCorrectionError(f"no page at {Path('pages') / relative}")
    return target, (Path("pages") / relative).as_posix()


def _split_front_matter(text: str) -> tuple[dict[str, Any] | None, str]:
    """(front matter mapping, body); the mapping is None when the page has none."""
    if not text.startswith("---\n"):
        return None, text
    front, separator, body = text[4:].partition("\n---\n")
    if not separator or len(front) > _FRONT_MATTER_CHARS:
        return None, text
    try:
        loaded = yaml.safe_load(front)
    except yaml.YAMLError:
        return None, text
    if not isinstance(loaded, dict):
        return None, text
    return dict(loaded), body


def _lead_span(lines: list[str]) -> tuple[int, int] | None:
    """[start, end) of the first prose paragraph: not a heading, fence, table or list."""
    fenced = False
    start: int | None = None
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("```"):
            if start is not None:
                return start, index
            fenced = not fenced
            continue
        if fenced:
            continue
        prose = bool(line) and not line.startswith(("#", "|", "---", "- ", "* ", "> "))
        if start is None:
            if prose:
                start = index
            continue
        if not prose:
            return start, index
    return (start, len(lines)) if start is not None else None


def _insert_index(lines: list[str]) -> int:
    """Where a lead goes when the page has none: after the title and its blank line."""
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
    return index


def _with_history(body_lines: list[str], entry: str) -> list[str]:
    """The body with ``entry`` appended under the trailing History section."""
    lines = list(body_lines)
    while lines and not lines[-1].strip():
        lines.pop()
    heading_at = next(
        (index for index, raw in enumerate(lines) if raw.strip().casefold() == HISTORY_HEADING.casefold()),
        None,
    )
    if heading_at is not None and not any(
        raw.lstrip().startswith("#") and 0 < len(raw.lstrip().split(" ")[0]) <= 2
        for raw in lines[heading_at + 1:]
    ):
        return [*lines, entry]
    return [*lines, "", HISTORY_HEADING, "", entry]


def _quoted(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _serialize(front: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1_000).strip()
    return f"---\n{dumped}\n---\n{body}"


def _update_index_line(library: Path, relative: str, title: str, description: str) -> bool:
    index_path = library / "INDEX.md"
    try:
        existing = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    marker = f"]({relative})"
    if marker not in existing:
        return False
    line = f"- [{title}]({relative}) — {description}\n"
    lines = existing.splitlines(keepends=True)
    changed = [line if marker in old else old for old in lines]
    if changed == lines:
        return False
    _atomic_write_text(index_path, "".join(changed))
    return True


def _title_of(front: dict[str, Any], body: str, page: Path) -> str:
    title = str(front.get("title") or front.get("name") or "").strip()
    if title:
        return title
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem


def correct_page(
    library: str | Path,
    page: str | Path,
    *,
    statement: str,
    reason: str,
    description: str = "",
    corrected_by: str = "operator",
    scope: str = "project",
    vertical: str = "",
    global_root: str | Path | None = None,
    now: float | None = None,
) -> PageCorrection:
    """Replace the page's lead (and summary) with ``statement``; keep what it said.

    ``statement`` becomes the first paragraph of the body, ``description`` the
    front matter summary when given, and ``reason`` explains in the History
    section why the earlier wording was wrong. The earlier file is copied to
    ``<library>/.history/`` before the page is rewritten. When ``global_root``
    is given the knowledge journal records the correction.
    """
    library = Path(library).expanduser().resolve()
    statement = _one_paragraph(statement, name="corrected statement")
    reason = _one_paragraph(reason, name="reason")
    description = " ".join(str(description or "").split())
    if len(description) > _TEXT_LIMIT:
        raise PageCorrectionError(f"the description is longer than {_TEXT_LIMIT} characters")
    corrected_by = " ".join(str(corrected_by or "").split()) or "operator"
    target, relative = _resolve_page(library, page)
    try:
        original = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PageCorrectionError(f"cannot read {relative}: {exc}") from exc
    text = original.replace("\r\n", "\n")
    front, body = _split_front_matter(text)
    if front is None:
        front = {}
        body = text
    title = _title_of(front, body, target)
    previous_description = str(front.get("description") or "").strip()
    page_kind = str(front.get("kind") or "").strip().lower() or "page"
    source = str(front.get("source") or "").strip()

    body_lines = body.split("\n")
    span = _lead_span(body_lines)
    if span is None:
        previous_lead = ""
        at = _insert_index(body_lines)
        body_lines[at:at] = [statement, ""]
    else:
        start, end = span
        previous_lead = " ".join(" ".join(body_lines[start:end]).split())
        body_lines[start:end] = [statement]
    stamp = _utc(now)
    date = stamp.date().isoformat()
    entry = f"- {date}, corrected by {corrected_by}: {reason}"
    if previous_lead and previous_lead != statement:
        entry += f" The page previously said: {_quoted(previous_lead)}"
    if description and previous_description and previous_description != description:
        entry += f" Its summary was: {_quoted(previous_description)}"
    body_lines = _with_history(body_lines, entry)
    body_text = "\n".join(body_lines).rstrip("\n") + "\n"

    front.setdefault("title", title)
    if description:
        front["description"] = description
    elif not previous_description:
        front["description"] = statement
    front["corrected"] = date
    front["corrected_by"] = corrected_by
    new_text = _serialize(front, "\n" + body_text)

    history_dir = library / HISTORY_DIRNAME
    history_copy = history_dir / Path(relative).relative_to("pages").with_name(
        f"{target.stem}.{stamp.strftime('%Y%m%dT%H%M%SZ')}{target.suffix}"
    )
    history_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, history_copy)
    _atomic_write_text(target, new_text)
    index_updated = _update_index_line(library, relative, title, str(front.get("description") or ""))

    if global_root is not None:
        append_knowledge_event(
            global_root,
            kind="corrected",
            scope=scope,
            vertical=vertical,
            path=relative,
            title=title,
            source_project=source,
            role=corrected_by,
            page_kind=page_kind,
            note=reason,
        )
    return PageCorrection(
        page=target,
        relative=relative,
        title=title,
        page_kind=page_kind,
        source=source,
        corrected=date,
        previous_description=previous_description,
        description=str(front.get("description") or ""),
        previous_lead=previous_lead,
        statement=statement,
        history_copy=history_copy,
        index_updated=index_updated,
    )


__all__ = [
    "HISTORY_DIRNAME",
    "HISTORY_HEADING",
    "PageCorrection",
    "PageCorrectionError",
    "correct_page",
]
