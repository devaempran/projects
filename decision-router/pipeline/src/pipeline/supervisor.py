"""`start`: run the whole pipeline from one command.

The user supplies Redis, a config file and events written to the ingestion stream. This process does the rest:
  - checks the config and Redis, and creates the streams and consumer groups;
  - starts the decisionLM server if the configured URL is local and nothing is answering there yet;
  - serves the live dashboard when [dashboard] enabled is true (the default);
  - runs [run] deciders decider processes (and, if [run] workers is true, a logging worker per decision stream);
  - restarts any process that dies (with backoff), and logs a status line every [run] status_every_s seconds;
  - on Ctrl+C / SIGTERM lets each decider finish the message in flight, then stops everything.

usage: python run.py start [--config PATH]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import redis

from pipeline import config, runtime

RUN_PY = os.path.join(config.ROOT, "run.py")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
RESTART_BACKOFF_MAX_S = 60
HEALTHY_AFTER_S = 30  # a child that ran this long before dying starts again from the shortest backoff


@dataclass
class Child:
    """One supervised process."""
    name: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    stop_file: str | None = None  # for children that watch for it (runtime.install_signal_handlers)
    grace_s: float = 150
    proc: subprocess.Popen | None = None
    started_at: float = 0.0
    restarts: int = 0
    next_start: float = 0.0
    backoff_s: float = 0.5

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # so Ctrl+C in this console is not sent twice
        env = dict(os.environ, **self.env)
        if self.stop_file:
            env[runtime.STOP_FILE_ENV] = self.stop_file
        self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=env, creationflags=flags)
        self.started_at = time.time()

    def request_stop(self) -> None:
        if not self.alive():
            return
        if self.stop_file:
            open(self.stop_file, "w").close()  # a graceful stop: the child finishes its message and exits
        else:
            self.proc.terminate()

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()
            self.proc.wait()


def service_url_is_local(cfg: config.Config) -> bool:
    return (urlsplit(cfg.url).hostname or "") in LOCAL_HOSTS


def http_ok(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def service_python(cfg: config.Config) -> str:
    if cfg.service_python:
        return cfg.service_python
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = os.path.join(cfg.service_dir, ".venv", *rel)
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def plan(cfg: config.Config, service_running: bool, stop_file: str | None = None) -> list[Child]:
    """The processes to run for this config. Pipeline children stop gracefully when `stop_file` appears."""
    env = {"PIPELINE_CONFIG": cfg.path, "PYTHONUNBUFFERED": "1"}
    children: list[Child] = []
    if cfg.service_start and not service_running and service_url_is_local(cfg):
        children.append(Child("decisionLM", [service_python(cfg), "run.py"], cwd=cfg.service_dir, grace_s=15,
                              env={"PORT": str(urlsplit(cfg.url).port or 5001), "PYTHONUNBUFFERED": "1"}))
    for i in range(1, cfg.deciders + 1):
        children.append(Child(f"decider-{i}", [sys.executable, RUN_PY, "decider", f"{cfg.node_name}-decider-{i}"], env=env,
                              stop_file=stop_file, grace_s=cfg.shutdown_grace_s))
    if cfg.dashboard_enabled:
        children.append(Child("dashboard", [sys.executable, RUN_PY, "dashboard"], env=env, stop_file=stop_file, grace_s=10))
    if cfg.workers:
        for stream in cfg.decision_streams:
            argv = [sys.executable, RUN_PY, "worker", stream, f"{cfg.node_name}-worker-{stream}"]
            children.append(Child(f"worker[{stream}]", argv, env=env, stop_file=stop_file, grace_s=cfg.shutdown_grace_s))
    return children


def status_line(r: redis.Redis, cfg: config.Config, children: list[Child], service_ok: bool) -> str:
    try:
        lag = pending = None
        for g in r.xinfo_groups(cfg.ingestion):
            if g["name"] == cfg.ingestion_group:
                lag, pending = g.get("lag"), g["pending"]
        routed = sum(r.xlen(s) for s in cfg.decision_streams)
        text = f"in-queue={lag} deciding={pending} routed={routed} dead-lettered={r.xlen(cfg.dlq)}"
    except redis.RedisError as e:
        text = f"redis unavailable ({e})"
    down = [c.name for c in children if not c.alive()]
    line = f"[start] {text} | decisionLM {'up' if service_ok else 'DOWN (events wait)'}"
    return line + (f" | restarting: {', '.join(down)}" if down else "")


class Supervisor:
    def __init__(self, cfg: config.Config, children: list[Child], r: redis.Redis | None = None, tick_s: float = 1.0):
        self.cfg, self.children, self.r, self.tick_s = cfg, children, r, tick_s

    def run(self) -> None:
        for c in self.children:
            c.start()
            print(f"[start] started {c.name} (pid {c.proc.pid})", flush=True)
        last_status = time.time()
        try:
            while not runtime.stop.is_set():
                self.tick()
                if self.r is not None and self.cfg.status_every_s and time.time() - last_status >= self.cfg.status_every_s:
                    print(status_line(self.r, self.cfg, self.children, http_ok(self.cfg.health_url)), flush=True)
                    last_status = time.time()
                runtime.stop.wait(self.tick_s)
        finally:
            self.shutdown()

    def tick(self) -> None:
        """Restart any child that has exited, after a backoff that doubles for a child that keeps dying."""
        now = time.time()
        for c in self.children:
            if c.alive():
                continue
            if c.next_start == 0.0:  # just noticed it died
                code = c.proc.returncode if c.proc else None
                c.backoff_s = 1.0 if now - c.started_at >= HEALTHY_AFTER_S else min(c.backoff_s * 2, RESTART_BACKOFF_MAX_S)
                c.next_start = now + c.backoff_s
                print(f"[start] {c.name} exited with code {code}; restarting in {c.backoff_s:.0f}s", flush=True)
            elif now >= c.next_start:
                c.restarts += 1
                c.next_start = 0.0
                c.start()
                print(f"[start] restarted {c.name} (pid {c.proc.pid}, restart #{c.restarts})", flush=True)

    def shutdown(self) -> None:
        """Ask every child to stop at once, then wait for each up to its grace period, then kill the stragglers.
        The service goes last so deciders finishing a message can still reach it."""
        print("[start] stopping (each decider finishes the message in flight; Ctrl+C again to kill)...", flush=True)
        workers = [c for c in self.children if c.name != "decisionLM"]
        service = [c for c in self.children if c.name == "decisionLM"]
        try:
            for group in (workers, service):
                for c in group:
                    c.request_stop()
                deadline = time.time() + max((c.grace_s for c in group), default=0)
                for c in group:
                    while c.alive() and time.time() < deadline:
                        time.sleep(0.1)
                    if c.alive():
                        print(f"[start] {c.name} did not stop in time; killing it (its message is reclaimed later)", flush=True)
                        c.kill()
        except KeyboardInterrupt:
            for c in self.children:
                c.kill()
        print("[start] stopped", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="path to the config file (default: $PIPELINE_CONFIG or config/pipeline.toml)")
    args = ap.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        raise SystemExit(f"config error: {e}") from None
    if cfg.has_placeholders:
        raise SystemExit(f"{cfg.path} still has <REPLACE ...> placeholders: set [decision] question and the [[options]] first.")
    r = config.connect(cfg)
    try:
        r.ping()
    except redis.RedisError as e:
        raise SystemExit(f"cannot reach Redis at {cfg.host}:{cfg.port}: {e}") from None
    config.ensure_groups(r, cfg)

    runtime.install_signal_handlers("start")
    service_running = http_ok(cfg.health_url)
    stop_file = os.path.join(tempfile.mkdtemp(prefix="decision-router-"), "stop")
    children = plan(cfg, service_running, stop_file)
    if cfg.service_start and not service_running and not any(c.name == "decisionLM" for c in children):
        print(f"[start] decisionLM is not answering at {cfg.health_url} and is not local, so it is not started; "
              "events will wait until it is up", flush=True)
    if any(c.name == "decisionLM" for c in children) and not os.path.isdir(cfg.service_dir):
        raise SystemExit(f"[service] dir {cfg.service_dir} does not exist; set [service] dir, or [service] start = false "
                         "and run decisionLM yourself")
    print(f"[start] {cfg.ingestion} -> {len(cfg.options)} options {cfg.option_names}; {cfg.deciders} decider(s); "
          f"write events to the '{cfg.ingestion}' stream with field '{cfg.context_field}'", flush=True)
    if cfg.dashboard_enabled:
        print(f"[start] dashboard: http://{cfg.dashboard_host}:{cfg.dashboard_port}", flush=True)
    Supervisor(cfg, children, r).run()


if __name__ == "__main__":
    main()
