"""The reasoner's answer handling, with a fake model."""
import random
import time
import zlib

import pytest

from decisionlm import errors, model, settings, tier2


class FakeLlama:
    def __init__(self, text, delay=0.0):
        self.text, self.delay, self.resets, self.max_tokens = text, delay, 0, None

    def tokenize(self, b, add_bos=False, special=False):
        return list(range(len(b) // 4 + 1)) if len(b) > 1 else [7]

    def reset(self):
        self.resets += 1

    def create_completion(self, prompt, max_tokens, stopping_criteria=None, **kw):
        self.max_tokens = max_tokens
        if self.delay:
            time.sleep(self.delay)
        return {"choices": [{"text": self.text}], "usage": {"completion_tokens": 5}}


@pytest.fixture
def run(monkeypatch):
    def _run(llm, options=("a", "b", "c", "d"), **kw):
        monkeypatch.setitem(model._state, "llm", llm)
        for i, label in enumerate(settings.LABELS):
            monkeypatch.setitem(model.label_ids, label, i)
        return tier2.reason("ctx", "q?", list(options), **kw)
    return _run


def shuffled_position(k):
    perm = list(range(k))
    random.Random(zlib.crc32(b"q?")).shuffle(perm)
    return perm


@pytest.mark.parametrize("text,expected", [
    ("thinking</think>\n\nAnswer: C", 2),
    ("</think>\n\n**Answer: (b)**", None),       # lower case is not accepted
    ("</think>\n\nAnswer: A\n\nAnswer: D", 3),   # the last answer line wins
    ("no think block\nAnswer: A", None),         # an answer inside unfinished thinking does not count
    ("</think>\n\nAnswer: Z", None),             # outside the option range
])
def test_parse_answer(text, expected):
    assert tier2.parse_answer(text, list("ABCD")) == expected


def test_answer_letter_maps_back_through_the_option_shuffle(run):
    r = run(FakeLlama("thinking...</think>\n\nAnswer: C"))
    assert r["index"] == shuffled_position(4)[2] and not r["forced"]


def test_no_answer_line_falls_back_to_a_forced_answer(run, monkeypatch):
    monkeypatch.setattr(tier2, "_forced_answer", lambda llm, prompt, thinking, labels: 1)
    r = run(FakeLlama("no answer line at all"))
    assert r["forced"] and r["index"] == shuffled_position(4)[1]


def test_token_budget_caps_generation(run):
    llm = FakeLlama("</think>\n\nAnswer: A")
    run(llm, max_think_tokens=100)
    assert llm.max_tokens == 100


def test_wall_clock_limit_is_reported_and_falls_back(run, monkeypatch):
    monkeypatch.setattr(tier2, "_forced_answer", lambda *a: 0)
    r = run(FakeLlama("partial thinking", delay=1.2), max_think_seconds=1)
    assert r["timed_out"] and r["forced"]


def test_oversized_prompt_is_rejected(run):
    with pytest.raises(errors.PromptTooLongError):
        run(FakeLlama("x"), options=("a" * 40000, "b"))


def test_failed_generation_resets_the_cache_and_releases_the_lock(run):
    llm = FakeLlama("x")
    llm.create_completion = lambda **k: (_ for _ in ()).throw(RuntimeError("cuda error"))
    with pytest.raises(RuntimeError):
        run(llm)
    assert llm.resets >= 2
    assert model.lock.acquire(blocking=False)
    model.lock.release()
