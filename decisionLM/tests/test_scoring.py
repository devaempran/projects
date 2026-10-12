"""
Validation checks against the local llama-cpp-python model.

    .venv\\Scripts\\python.exe test_scoring.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from decisionlm import errors, tier1  # noqa: E402


def check_sums_to_one(result):
    total = sum(c["probability"] for c in result["choices"])
    assert math.isclose(total, 1.0, abs_tol=1e-6), f"probabilities sum to {total}, not 1"
    print(f"  [ok] probabilities sum to {total:.6f}")


def check_winner_is_argmax(result):
    best = max(result["choices"], key=lambda c: c["probability"])
    assert best["label"] == result["winner"], "winner doesn't match argmax of restricted probs"
    best_by_logprob = max(result["choices"], key=lambda c: c["logprob"])
    assert best_by_logprob["label"] == result["winner"], "winner doesn't match greedy logprob selection"
    print(f"  [ok] winner '{result['winner']}' matches greedy selection restricted to given labels")


def test_basic_example():
    print("test_basic_example")
    result = tier1.score(
        context="James has a Tesla Model Y.",
        question="What is James's car's make and model?",
        choices=[
            {"label": "A", "text": "Tesla Model S"},
            {"label": "B", "text": "Tesla Model Y"},
            {"label": "C", "text": "Toyota Camry"},
        ],
    )
    check_sums_to_one(result)
    check_winner_is_argmax(result)
    assert result["winner"] == "B", f"expected B, got {result['winner']}"
    print(f"  [ok] winner is B as expected ({result['choices']})")
    return result


def test_reordered_choices():
    print("test_reordered_choices")
    # Same question, same answer content, but now the correct answer sits at
    # label A instead of B. A well-calibrated model should follow content,
    # not position.
    result = tier1.score(
        context="James has a Tesla Model Y.",
        question="What is James's car's make and model?",
        choices=[
            {"label": "A", "text": "Tesla Model Y"},
            {"label": "B", "text": "Tesla Model S"},
            {"label": "C", "text": "Toyota Camry"},
        ],
    )
    check_sums_to_one(result)
    check_winner_is_argmax(result)
    if result["winner"] != "A":
        print(f"  [WARN] possible answer-position sensitivity: correct content moved to A, "
              f"but model still picked {result['winner']}")
    else:
        print("  [ok] model followed content to label A after reordering (no position bias detected)")
    return result


def test_invalid_label_errors_clearly():
    print("test_invalid_label_errors_clearly")
    try:
        tier1.score(
            context="",
            question="Pick one.",
            choices=[
                {"label": "Yes definitely", "text": "foo"},
                {"label": "No", "text": "bar"},
            ],
        )
        print("  [FAIL] expected a ScoringError for a multi-token label, got none")
    except errors.ScoringError as e:
        print(f"  [ok] got a clear error instead of silently mis-scoring: {e}")


def test_duplicate_labels_rejected():
    print("test_duplicate_labels_rejected")
    try:
        tier1.score(context="", question="Pick one.", choices=[
            {"label": "A", "text": "foo"},
            {"label": "A", "text": "bar"},
        ])
        print("  [FAIL] expected a ScoringError for duplicate labels")
    except errors.ScoringError as e:
        print(f"  [ok] {e}")


if __name__ == "__main__":
    test_basic_example()
    test_reordered_choices()
    test_invalid_label_errors_clearly()
    test_duplicate_labels_rejected()
    print("\nAll checks completed.")
