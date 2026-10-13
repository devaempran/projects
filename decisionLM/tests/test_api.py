"""HTTP layer and the decide cascade, with the model faked."""
import pytest

from decisionlm import decide, errors, tier1, tier2

GOOD = {"Context": "c", "Question": "q?", "Options": ["a", "b", "c"]}

INVALID_BODIES = {
    "missing Question": {"Context": "c", "Options": ["a", "b"]},
    "blank Question": {"Question": "  ", "Options": ["a", "b"]},
    "non-string Question": {"Question": 5, "Options": ["a", "b"]},
    "non-string Context": {"Context": 5, "Question": "q", "Options": ["a", "b"]},
    "one option": {"Question": "q", "Options": ["a"]},
    "no options": {"Question": "q"},
    "options not a list": {"Question": "q", "Options": "ab"},
    "duplicate options": {"Question": "q", "Options": ["a", "a"]},
    "empty option": {"Question": "q", "Options": ["a", " "]},
    "non-string option": {"Question": "q", "Options": ["a", 2]},
    "27 options": {"Question": "q", "Options": [str(i) for i in range(27)]},
}
BAD_NUMBERS = {
    "theta NaN": {"theta": float("nan")}, "theta inf": {"theta": float("inf")}, "theta negative": {"theta": -1},
    "theta string": {"theta": "hi"}, "theta bool": {"theta": True}, "max_think_tokens 0": {"max_think_tokens": 0},
    "max_think_tokens float": {"max_think_tokens": 10.5}, "max_think_tokens huge": {"max_think_tokens": 10**9},
    "max_think_seconds negative": {"max_think_seconds": -3},
}


def fake_scores(monkeypatch, probs, mass=1.0):
    """Replace tier 1 with fixed probabilities."""
    def score(context, question, options):
        return {"choices": [{"label": "ABC"[i], "text": o, "probability": probs[i], "logprob": 0}
                            for i, o in enumerate(options)], "total_label_probability_mass": mass}
    monkeypatch.setattr(tier1, "score_options", score)


# ---- input validation: never reaches the model ------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/score", "/api/decide"])
@pytest.mark.parametrize("name", INVALID_BODIES)
def test_invalid_input_is_a_400(client, path, name):
    r = client.post(path, json=INVALID_BODIES[name])
    assert r.status_code == 400 and "error" in r.get_json()


@pytest.mark.parametrize("path", ["/api/score", "/api/decide"])
@pytest.mark.parametrize("raw", ["not json", "[1,2]", ""], ids=["not-json", "array", "empty"])
def test_malformed_body_is_a_400(client, path, raw):
    assert client.post(path, data=raw, content_type="application/json").status_code == 400


@pytest.mark.parametrize("name", BAD_NUMBERS)
def test_bad_numeric_parameters_are_rejected_before_the_model(client, name):
    assert client.post("/api/decide", json={**GOOD, **BAD_NUMBERS[name]}).status_code == 400


def test_oversized_body_is_a_413_json(client):
    r = client.post("/api/decide", json={**GOOD, "Context": "x" * 2_000_000})
    assert r.status_code == 413 and r.is_json


def test_unknown_route_and_wrong_method_are_json(client):
    for r in (client.get("/api/nope"), client.get("/api/decide")):
        assert r.status_code in (404, 405) and r.is_json


# ---- error mapping ------------------------------------------------------------------------------------
@pytest.mark.parametrize("exc,status", [(errors.ModelLoadError("no model"), 503), (errors.PromptTooLongError("long"), 413)])
def test_model_errors_map_to_status_codes(client, monkeypatch, exc, status):
    monkeypatch.setattr(tier1, "score_options", lambda *a: (_ for _ in ()).throw(exc))
    assert client.post("/api/decide", json=GOOD).status_code == status


def test_unexpected_exception_is_a_json_500_without_detail(client, monkeypatch):
    monkeypatch.setattr(tier1, "score_options", lambda *a: (_ for _ in ()).throw(RuntimeError("kaboom")))
    r = client.post("/api/decide", json=GOOD)
    assert r.status_code == 500 and r.get_json() == {"error": "internal error"}


# ---- the cascade --------------------------------------------------------------------------------------
@pytest.fixture
def reasoner(monkeypatch):
    calls = []

    def reason(context, question, options, budget=None, seconds=None):
        calls.append((budget, seconds))
        return {"index": 2, "forced": False, "timed_out": False, "tokens": 10, "latency_s": 0.1}
    monkeypatch.setattr(tier2, "reason", reason)
    return calls


def test_confident_answer_stays_on_the_fast_path(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.9, 0.05, 0.05])
    d = client.post("/api/decide", json=GOOD).get_json()
    assert (d["source"], d["answer"]) == ("fast", "a") and not reasoner


def test_below_theta_the_reasoner_decides(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.9, 0.05, 0.05])
    d = client.post("/api/decide", json={**GOOD, "theta": 0.95}).get_json()
    assert (d["source"], d["answer"], d["tier1_answer"]) == ("reasoner", "c", "a")


def test_theta_zero_never_escalates_and_theta_above_one_always_does(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.9, 0.05, 0.05])
    assert client.post("/api/decide", json={**GOOD, "theta": 0}).get_json()["source"] == "fast"
    assert client.post("/api/decide", json={**GOOD, "theta": 5}).get_json()["source"] == "reasoner"


def test_limits_are_passed_to_the_reasoner(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.9, 0.05, 0.05])
    client.post("/api/decide", json={**GOOD, "theta": 5, "max_think_tokens": 123, "max_think_seconds": 7})
    assert reasoner[-1] == (123, 7)


def test_low_label_mass_escalates_even_when_confident(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.9, 0.05, 0.05], mass=0.1)
    assert client.post("/api/decide", json=GOOD).get_json()["source"] == "reasoner"


def test_exact_tie_goes_to_the_first_option(client, monkeypatch, reasoner):
    fake_scores(monkeypatch, [1 / 3, 1 / 3, 1 / 3])
    d = client.post("/api/decide", json={**GOOD, "theta": 0.1}).get_json()
    assert (d["answer"], d["source"]) == ("a", "fast")


def test_reasoner_failure_returns_tier1_answer_flagged_low_confidence(client, monkeypatch):
    fake_scores(monkeypatch, [0.5, 0.3, 0.2])
    monkeypatch.setattr(tier2, "reason", lambda *a: (_ for _ in ()).throw(RuntimeError("reasoner exploded")))
    r = client.post("/api/decide", json=GOOD)
    d = r.get_json()
    assert r.status_code == 200 and d["low_confidence"] and d["answer"] == "a" and "exploded" in d["reasoner_error"]


def test_decide_can_be_used_without_http(monkeypatch, reasoner):
    fake_scores(monkeypatch, [0.4, 0.4, 0.2])
    out = decide.decide("ctx", "q?", ["a", "b", "c"])
    assert out["source"] == "reasoner" and out["answer"] == "c"
