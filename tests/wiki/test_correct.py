"""A wrong knowledge page is corrected where readers look first, and what it said stays on record."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.life.knowledge_recall import KnowledgeRoot, MarkdownKnowledgeRecall
from argus.wiki.correct import HISTORY_DIRNAME, PageCorrectionError, correct_page
from argus.wiki.journal import journal_path

PAGE = (
    "---\n"
    "title: Roofline evaluators should not trust self-reported regime labels\n"
    "description: Recompute the regime from independent calibration, not from the control file.\n"
    "kind: lesson\n"
    "source: s-02d3c282/c2ff7e67cdf2\n"
    "created: 2026-09-30\n"
    "confidence: high\n"
    "---\n"
    "\n"
    "# Roofline evaluators should not trust self-reported regime labels\n"
    "\n"
    "## What happened\n"
    "\n"
    "The evaluator accepted the regime stored in the control file, so any\n"
    "timing passed as correctly classified.\n"
    "\n"
    "## Evidence\n"
    "\n"
    "- `src/analysis.py`\n"
)
NOW = 1_790_760_000.0  # 2026-09-30T09:20:00Z


def library(root: Path, *, text: str = PAGE, relative: str = "lessons/regime.md") -> Path:
    page = root / "pages" / relative
    page.parent.mkdir(parents=True)
    page.write_text(text, encoding="utf-8")
    (root / "INDEX.md").write_text(
        "# Research knowledge\n\n## Lessons\n\n"
        f"- [Roofline evaluators should not trust self-reported regime labels](pages/{relative})"
        " — Recompute the regime from independent calibration, not from the control file.\n",
        encoding="utf-8",
    )
    return root


def test_correction_replaces_the_lead_and_summary_and_keeps_the_earlier_wording(tmp_path):
    root = library(tmp_path / "wiki")
    done = correct_page(
        root, "pages/lessons/regime.md",
        statement="The evaluator did classify the regime itself; the mistake was using the control "
                  "file's peak FLOP figure, which came from the spec sheet rather than a measurement.",
        reason="Re-reading analysis.py shows classify_regime() recomputes the regime from timings.",
        description="Recompute peak FLOP from a measurement, not from the spec sheet.",
        corrected_by="operator", scope="vertical", vertical="research", global_root=tmp_path / "home",
        now=NOW,
    )
    text = (root / "pages" / "lessons" / "regime.md").read_text(encoding="utf-8")
    front, _, body = text[4:].partition("\n---\n")
    assert "corrected: '2026-09-30'" in front or "corrected: 2026-09-30" in front
    assert "corrected_by: operator" in front
    assert "description: Recompute peak FLOP from a measurement, not from the spec sheet." in front
    assert "source: s-02d3c282/c2ff7e67cdf2" in front and "confidence: high" in front
    # The corrected statement is the first prose paragraph; the old one is gone from the lead.
    assert body.index("The evaluator did classify the regime itself") < body.index("## Evidence")
    assert "The evaluator accepted the regime stored in the control file, so any\ntiming" not in body
    history = body[body.index("## History"):]
    assert "2026-09-30, corrected by operator: Re-reading analysis.py shows" in history
    assert 'The page previously said: "The evaluator accepted the regime stored in the control file, so any timing passed as correctly classified."' in history
    assert 'Its summary was: "Recompute the regime from independent calibration, not from the control file."' in history
    assert done.previous_lead.startswith("The evaluator accepted the regime")
    assert done.relative == "pages/lessons/regime.md"
    # The earlier file is kept byte for byte, out of the browsed and recalled tree.
    assert done.history_copy == root / HISTORY_DIRNAME / "lessons" / "regime.20260930T092000Z.md"
    assert done.history_copy.read_text(encoding="utf-8") == PAGE
    # The index entry follows the new summary.
    assert done.index_updated
    assert "— Recompute peak FLOP from a measurement, not from the spec sheet." in (root / "INDEX.md").read_text()
    # The journal records the correction with its reason.
    rows = [json.loads(line) for line in journal_path(tmp_path / "home").read_text().splitlines()]
    assert [row["kind"] for row in rows] == ["corrected"]
    assert rows[0]["path"] == "pages/lessons/regime.md" and rows[0]["scope"] == "vertical"
    assert rows[0]["vertical"] == "research" and rows[0]["role"] == "operator"
    assert rows[0]["source_project"] == "s-02d3c282/c2ff7e67cdf2" and rows[0]["page_kind"] == "lesson"
    assert rows[0]["note"].startswith("Re-reading analysis.py")


def test_a_second_correction_extends_the_same_history_section(tmp_path):
    root = library(tmp_path / "wiki")
    correct_page(root, "lessons/regime.md", statement="First correction.", reason="First reason.", now=NOW)
    correct_page(root, "lessons/regime.md", statement="Second correction.", reason="Second reason.", now=NOW + 3600)
    body = (root / "pages" / "lessons" / "regime.md").read_text(encoding="utf-8")
    assert body.count("## History") == 1
    history = body[body.index("## History"):]
    assert history.index("First reason.") < history.index("Second reason.")
    assert 'Second reason. The page previously said: "First correction."' in history
    assert body.index("Second correction.") < body.index("## Evidence")
    assert "First correction." not in body[:body.index("## History")]
    kept = sorted(path.name for path in (root / HISTORY_DIRNAME / "lessons").iterdir())
    assert kept == ["regime.20260930T092000Z.md", "regime.20260930T102000Z.md"]


def test_a_page_without_front_matter_or_lead_gets_both(tmp_path):
    root = library(tmp_path / "wiki", text="# Only a title\n\n- a list, not prose\n", relative="notes/bare.md")
    done = correct_page(root, "notes/bare.md", statement="What is true.", reason="Why.", now=NOW)
    text = (root / "pages" / "notes" / "bare.md").read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: Only a title\ndescription: What is true.\n")
    body = text.split("\n---\n", 1)[1]
    assert body.index("# Only a title") < body.index("What is true.") < body.index("- a list, not prose")
    assert done.previous_lead == "" and "previously said" not in body


@pytest.mark.parametrize("page", ["../INDEX.md", "pages/.hidden/x.md", "pages/_retired/x.md", "missing.md", "x.txt"])
def test_only_a_real_page_under_pages_is_corrected(tmp_path, page):
    root = library(tmp_path / "wiki")
    with pytest.raises(PageCorrectionError):
        correct_page(root, page, statement="s", reason="r")


def test_empty_statement_or_reason_is_refused_before_anything_changes(tmp_path):
    root = library(tmp_path / "wiki")
    before = (root / "pages" / "lessons" / "regime.md").read_bytes()
    for kwargs in ({"statement": " ", "reason": "r"}, {"statement": "s", "reason": ""}):
        with pytest.raises(PageCorrectionError):
            correct_page(root, "lessons/regime.md", **kwargs)
    assert (root / "pages" / "lessons" / "regime.md").read_bytes() == before
    assert not (root / HISTORY_DIRNAME).exists()


def test_recall_shows_the_correction_and_the_new_summary_and_not_the_history(tmp_path):
    root = library(tmp_path / "wiki")
    recall = MarkdownKnowledgeRecall(
        tmp_path / "index.sqlite3",
        [KnowledgeRoot("research knowledge", root / "pages", root, scope="vertical", vertical="research", library=root)],
    )
    before = recall.recall("roofline regime classification from the control file")
    assert "corrected" not in before.text
    correct_page(
        root, "lessons/regime.md",
        statement="The evaluator classifies the regime itself; the spec-sheet peak was the mistake.",
        reason="classify_regime() recomputes from timings; the mistaken input was the datasheet peak.",
        description="Measure peak FLOP; do not take it from the datasheet.",
        now=NOW,
    )
    after = recall.recall("roofline regime classification from the control file")
    assert after.hits and after.hits[0].document.meta.corrected == "2026-09-30"
    line = after.hits[0].line
    assert "corrected 2026-09-30" in line and "Measure peak FLOP; do not take it from the datasheet." in line
    # What the page said before lives in History, which recall does not search.
    document = after.hits[0].document
    from argus.life.knowledge_recall import searchable_body

    assert "previously said" not in searchable_body(document.content)
    assert "spec-sheet peak was the mistake" in searchable_body(document.content)
