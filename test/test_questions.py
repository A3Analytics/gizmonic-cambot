"""Question builder used before the JEV agreement check."""

import pytest

from host.witness_models.questions import questions_from_description

CANNED = """
A red square sits on a white background.
Entailed:
- The image shows a red square.
- The background is white.
Contradicted:
- The image shows a blue circle.
- The frame is empty.
"""


def test_canned_description_splits_entailed_and_contradicted():
    questions = questions_from_description(CANNED)
    entailed = [item for item in questions if item.expect == "yes"]
    contradicted = [item for item in questions if item.expect == "no"]
    assert [item.label for item in entailed] == [
        "The image shows a red square.",
        "The background is white.",
    ]
    assert [item.label for item in contradicted] == [
        "The image shows a blue circle.",
        "The frame is empty.",
    ]
    assert all("`photo`" in item.instructions for item in questions)
    assert len({item.name for item in questions}) == 4


def test_missing_heading_is_rejected():
    with pytest.raises(ValueError):
        questions_from_description("Entailed:\n- only one side\n")
