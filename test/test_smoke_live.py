"""Live model calls. Skipped, and reported, when that provider's key is unset."""

from __future__ import annotations

import os

import pytest

from host.witness_index.images import shape_jpeg
from host.witness_models.anthropic import AnthropicAdapter
from host.witness_models.jev import JevAdapter
from host.witness_models.openai import OpenAIAdapter
from host.witness_models.questions import questions_from_description

pytestmark = pytest.mark.live


def test_openai_smoke():
    key = os.environ.get("WITNESS_MODEL_KEY")
    if not key:
        pytest.skip("WITNESS_MODEL_KEY is not set")
    result = OpenAIAdapter(key).complete_text(shape_jpeg(), "What color is the square? One word.")
    assert result.status == "ok"
    assert result.text.strip()


def test_claude_smoke():
    key = os.environ.get("WITNESS_ANTHROPIC_KEY")
    if not key:
        pytest.skip("WITNESS_ANTHROPIC_KEY is not set")
    result = AnthropicAdapter(key).complete_text(shape_jpeg(), "What color is the square? One word.")
    assert result.status == "ok"
    assert result.text.strip()


def test_jev_smoke():
    key = os.environ.get("WITNESS_JEV_KEY")
    if not key:
        pytest.skip("WITNESS_JEV_KEY is not set")
    from host.witness_models.questions import NoulQuestion

    question = NoulQuestion("square", "Is there a red square in `photo`?", "red square", "yes")
    scores = JevAdapter(key).ask(shape_jpeg(), [question])
    assert scores["red square"] > 0.5


def test_jev_agrees_with_a_larger_model():
    jev_key = os.environ.get("WITNESS_JEV_KEY")
    if not jev_key:
        pytest.skip("WITNESS_JEV_KEY is not set")
    openai_key = os.environ.get("WITNESS_MODEL_KEY")
    anthropic_key = os.environ.get("WITNESS_ANTHROPIC_KEY")
    if not openai_key and not anthropic_key:
        pytest.skip("neither WITNESS_MODEL_KEY nor WITNESS_ANTHROPIC_KEY is set")
    jpeg = shape_jpeg()
    if openai_key:
        description = OpenAIAdapter(openai_key).describe(jpeg)
    else:
        description = AnthropicAdapter(anthropic_key).describe(jpeg)
    questions = questions_from_description(description)
    scores = JevAdapter(jev_key).ask(jpeg, questions)
    for question in questions:
        if question.expect == "yes":
            assert scores[question.label] >= 0.8
        else:
            assert scores[question.label] <= 0.2
