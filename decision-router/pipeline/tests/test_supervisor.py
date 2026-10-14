"""The `start` command: supervising child processes, and the whole pipeline from one command."""
import os
import signal
import subprocess
import sys
import threading
import time
import uuid

import pytest
from helpers import OPTIONS, RUN, SRC, Mock

from pipeline import config, runtime, supervisor
from pipeline.supervisor import Child, Supervisor

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
MINIMAL = '[decision]\nquestion = "q"\n[[options]]\nname = "a"\n[[options]]\nname = "b"\n'


def build(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return config.load(str(p), environ={})


@pytest.fixture
def minimal_cfg(tmp_path):
    return build(tmp_path, MINIMAL + "[run]\ndeciders = 2\nworkers = true\n[service]\nstart = false\n")


def test_consumer_names_carry_the_node_name_so_nodes_never_collide(tmp_path):
    def argv_names(node):
        cfg = build(tmp_path, MINIMAL + f'[run]\nnode_name = "{node}"\nworkers = true\n[service]\nstart = false\n')
        return [c.argv[-1] for c in supervisor.plan(cfg, True) if c.name.startswith(("decider", "worker"))]

    a, b = argv_names("node-a"), argv_names("node-b")
    assert a[0] == "node-a-decider-1" and not set(a) & set(b)


def test_node_name_defaults_to_the_hostname_and_rejects_spaces(tmp_path):
    import socket

    assert build(tmp_path, MINIMAL).node_name == socket.gethostname()
    with pytest.raises(config.ConfigError, match="node_name"):
        build(tmp_path, MINIMAL + '[run]\nnode_name = "a b"\n')


def test_plan_runs_the_configured_deciders_and_workers(minimal_cfg):
    names = [c.name for c in supervisor.plan(minimal_cfg, service_running=False)]
    assert names == ["decider-1", "decider-2", "dashboard", "worker[decision:a]", "worker[decision:b]", "worker[decision:review]"]


def test_plan_starts_the_service_only_when_asked_local_and_not_already_running(tmp_path):
    def first(text, running):
        return supervisor.plan(build(tmp_path, text), running)[0].name

    assert first(MINIMAL, False) == "decisionLM"
    assert first(MINIMAL, True) == "decider-1"                                           # already answering
    assert first(MINIMAL + "[service]\nstart = false\n", False) == "decider-1"           # the user runs it
    remote = MINIMAL.replace('question = "q"', 'question = "q"\nurl = "http://gpu-box:5001/api/decide"')
    assert first(remote, False) == "decider-1"                                           # not local


def test_a_child_that_dies_is_restarted_with_growing_backoff(minimal_cfg):
    child = Child("c", [sys.executable, "-c", "raise SystemExit(3)"])
    sup = Supervisor(minimal_cfg, [child], tick_s=0.05)
    child.start()
    child.proc.wait()
    sup.tick()                       # notices the exit and schedules a restart
    assert child.next_start > 0 and child.restarts == 0 and child.backoff_s == 1.0
    child.next_start = time.time() - 1
    sup.tick()                       # restarts it
    assert child.restarts == 1
    child.proc.wait()
    sup.tick()
    assert child.backoff_s == 2.0    # died quickly again: the delay doubled (1s, then 2s)


def test_shutdown_stops_children_and_kills_stragglers(minimal_cfg, tmp_path):
    ignore = ("import signal, time\nfor n in ('SIGTERM', 'SIGBREAK'):\n"
              "    getattr(signal, n, None) and signal.signal(getattr(signal, n), signal.SIG_IGN)\ntime.sleep(60)")
    stop_file = str(tmp_path / "stop")
    waits = ("from pipeline import runtime; runtime.install_signal_handlers('x'); "
             "runtime.stop.wait(60)")
    polite = Child("polite", [sys.executable, "-c", waits], env={"PYTHONPATH": SRC}, stop_file=stop_file, grace_s=10)
    stubborn = Child("stubborn", [sys.executable, "-c", ignore], grace_s=1)
    sup = Supervisor(minimal_cfg, [polite, stubborn])
    for c in sup.children:
        c.start()
    time.sleep(1.5)
    t0 = time.time()
    sup.shutdown()
    assert not polite.alive() and not stubborn.alive() and time.time() - t0 < 5


def test_run_exits_when_asked_to_stop(minimal_cfg):
    child = Child("c", SLEEP, grace_s=5)
    sup = Supervisor(minimal_cfg, [child], tick_s=0.05)
    threading.Timer(0.5, runtime.stop.set).start()
    sup.run()
    assert not child.alive()


def test_start_refuses_the_unfinished_template():
    out = subprocess.run([sys.executable, RUN, "start"], capture_output=True, text=True)
    assert out.returncode != 0 and "REPLACE" in out.stderr


def test_start_reports_unreachable_redis(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[redis]\nport = 1\n" + MINIMAL)
    out = subprocess.run([sys.executable, RUN, "start", "--config", str(p)], capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "cannot reach Redis" in out.stderr


@pytest.mark.slow
def test_one_command_decides_events_written_before_and_after_it_starts(tmp_path, mock_port):
    """The user story: Redis + a config + events in the ingestion stream; `start` does the rest."""
    from conftest import REDIS_HOST, REDIS_PORT

    prefix = "s" + uuid.uuid4().hex[:8]
    options = "".join(f'[[options]]\nname = "{n}"\nstream = "{prefix}:{n}"\n' for n in OPTIONS)
    cfg = build(tmp_path, f"""[redis]
host = "{REDIS_HOST}"
port = {REDIS_PORT}
[ingestion]
stream = "{prefix}:in"
group = "{prefix}-g"
[decision]
url = "http://127.0.0.1:{mock_port}/api/decide"
question = "Which one?"
timeout_s = 5
review_stream = ""
[reliability]
reclaim_idle_s = 60
block_ms = 500
[run]
deciders = 2
status_every_s = 1
[service]
start = false
{options}""")
    r = config.connect(cfg)
    mock = Mock(mock_port)
    mock.start()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        for i in range(5):  # written BEFORE start: the groups begin at 0, so these are not lost
            r.xadd(cfg.ingestion, {"id": f"before{i}", "payload": f"hello {i}"})
        proc = subprocess.Popen([sys.executable, RUN, "start", "--config", cfg.path], env=dict(os.environ, PYTHONUNBUFFERED="1"),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
        time.sleep(3)
        for i in range(5):
            r.xadd(cfg.ingestion, {"id": f"after{i}", "payload": f"hello {i}"})
        deadline = time.time() + 60
        while time.time() < deadline and sum(r.xlen(s) for s in cfg.decision_streams) < 10:
            time.sleep(0.5)
        routed = {rec["id"] for s in cfg.decision_streams for _i, rec in r.xrange(s)}
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        out, _ = proc.communicate(timeout=60)
        assert routed == {f"before{i}" for i in range(5)} | {f"after{i}" for i in range(5)}, out
        assert "in-queue=" in out and "stopped" in out, out
    finally:
        mock.stop()
        for key in list(r.scan_iter(f"{prefix}*")) + list(r.scan_iter(f"dedupe:{prefix}*")):
            r.delete(key)
