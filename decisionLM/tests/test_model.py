"""Checks against the real model. Skipped unless RUN_MODEL_TESTS=1: they load a second copy of the model (about
10 GB of VRAM), which will not fit while a decisionLM server is running."""
import math
import os

import llama_cpp
import pytest

from decisionlm import errors, settings, tier1

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_MODEL_TESTS") != "1" or getattr(llama_cpp, "__stub__", False)
    or not settings.load(environ={}).model_path.exists(),
    reason="set RUN_MODEL_TESTS=1 with llama-cpp-python and the model installed")

CAR = [{"label": "A", "text": "Tesla Model S"}, {"label": "B", "text": "Tesla Model Y"},
       {"label": "C", "text": "Toyota Camry"}]


def score(choices):
    return tier1.score(context="James has a Tesla Model Y.", question="What is James's car's make and model?", choices=choices)


def test_probabilities_sum_to_one_and_the_winner_is_the_argmax():
    result = score(CAR)
    assert math.isclose(sum(c["probability"] for c in result["choices"]), 1.0, abs_tol=1e-6)
    assert result["winner"] == max(result["choices"], key=lambda c: c["probability"])["label"] == "B"


def test_the_answer_follows_content_when_choices_are_reordered():
    reordered = [{"label": "A", "text": "Tesla Model Y"}, {"label": "B", "text": "Tesla Model S"},
                 {"label": "C", "text": "Toyota Camry"}]
    assert score(reordered)["winner"] == "A"


def test_a_multi_token_label_is_a_clear_error():
    with pytest.raises(errors.ScoringError):
        tier1.score("", "Pick one.", [{"label": "Yes definitely", "text": "foo"}, {"label": "No", "text": "bar"}])


def test_duplicate_labels_are_rejected():
    with pytest.raises(errors.ScoringError):
        tier1.score("", "Pick one.", [{"label": "A", "text": "foo"}, {"label": "A", "text": "bar"}])
