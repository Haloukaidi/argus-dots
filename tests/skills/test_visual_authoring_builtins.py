from pathlib import Path

import yaml

from argus.skills.builtins import iter_builtin_skill_texts, seed_builtin_skills

RESEARCH_ROOT = (
    Path(__file__).resolve().parents[2]
    / "argus"
    / "verticals"
    / "research"
    / "skills"
)


def _front_body(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def test_presentation_toolkit_notes_live_in_the_research_figure_studio() -> None:
    # PPT Master serves paper figures only, so the global adapter skill was
    # folded into the research vertical's figure studio.
    assert "engineer/presentation-master.md" not in dict(iter_builtin_skill_texts())
    front, body = _front_body(RESEARCH_ROOT / "engineer" / "paper-framework-figure-studio.md")
    assert set(front) == {"name", "description"}
    assert front["name"] == "Composing a conceptual paper figure"
    for term in ("ppt-master", "update_repo.py", '"${ARGUS_SKILL_PYTHON:-python3}"'):
        assert term in body
    assert "Do not call bare `python` or `python3`" in body


def test_seeding_preserves_existing_agent_document(tmp_path: Path) -> None:
    destination = tmp_path / "engineer" / "pdf-chat.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("operator-authored Skill\n", encoding="utf-8")
    seeded = seed_builtin_skills(tmp_path)
    assert seeded["engineer/pdf-chat.md"] is False
    assert destination.read_text(encoding="utf-8") == "operator-authored Skill\n"
