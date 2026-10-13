"""The command-line tools, run as subprocesses the way a user would."""
from helpers import RUN, run_cli


def test_help_lists_the_commands():
    import subprocess
    import sys

    out = subprocess.run([sys.executable, RUN, "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and all(c in out.stdout for c in ("decider", "worker", "producer", "setup", "inspect", "requeue"))


def test_setup_creates_the_streams_and_groups(r, cfg):
    r.delete(cfg.ingestion)
    out = run_cli(cfg, "setup")
    assert out.returncode == 0 and r.exists(cfg.ingestion) and cfg.decision_streams[0] in out.stdout


def test_requeue_moves_matching_events_back_and_strips_the_dead_letter_fields(r, cfg):
    r.xadd(cfg.dlq, {"id": "d1", "payload": "p", "ts": "1", "source_id": "1-0", "reason": "decisionLM rejected", "attempts": 1})
    r.xadd(cfg.dlq, {"id": "d2", "payload": "p", "ts": "1", "source_id": "1-1", "reason": "other", "attempts": 1})
    out = run_cli(cfg, "requeue", "--reason", "rejected")
    back = [rec for _id, rec in r.xrange(cfg.ingestion)]
    assert out.returncode == 0 and len(back) == 1 and back[0]["id"] == "d1" and "reason" not in back[0]
    assert r.xlen(cfg.dlq) == 1


def test_requeue_dry_run_changes_nothing(r, cfg):
    r.xadd(cfg.dlq, {"id": "d1", "payload": "p", "reason": "x"})
    out = run_cli(cfg, "requeue", "--dry-run")
    assert "would requeue 1" in out.stdout and r.xlen(cfg.dlq) == 1 and r.xlen(cfg.ingestion) == 0


def test_inspect_copes_with_a_missing_stream_and_shows_dead_letter_reasons(r, cfg):
    r.xadd(cfg.dlq, {"id": "d2", "payload": "p", "reason": "reason-text", "attempts": 1})
    r.delete(cfg.ingestion)
    out = run_cli(cfg, "inspect")
    assert out.returncode == 0 and "missing" in out.stdout and "reason=reason-text" in out.stdout


def test_producer_rejects_a_negative_count(cfg):
    out = run_cli(cfg, "producer", "--count", "-5")
    assert out.returncode != 0 and "must be >= 0" in out.stderr


def test_producer_writes_events_with_the_context_field(r, cfg):
    out = run_cli(cfg, "producer", "--count", "3")
    events = [rec for _id, rec in r.xrange(cfg.ingestion)]
    assert out.returncode == 0 and len(events) == 3 and all(e[cfg.context_field] and e["id"] for e in events)
