"""
Read the ingestion stream as its decider group. For each event, ask decisionLM (/api/decide) with
  Context  = event[context_field]    Question = config question    Options = config options
and forward the event to the stream of the option it chose (or to review_stream if the reasoner failed).

Failure handling, by kind:
  permanent  the event itself is bad (no usable context) or decisionLM rejects it (HTTP 4xx), so retrying
             can't help: straight to the DLQ with a `reason`.
  unreachable decisionLM is down/loading (connection refused, 502/503/504): the message waits (and is
             heart-beaten so nobody reclaims it) until /api/health answers, then is retried. No attempt burned.
  transient  timeouts, HTTP 5xx, malformed replies: retried in-process (retries, backoff), then left pending;
             XAUTOCLAIM redelivers it after reclaim_idle_s, and after max_deliveries deliveries it goes to the DLQ.
Forward + XACK (+ de-duplication on the event id) happen in one Lua script, so a message is never acked
without being forwarded and a re-delivered event is never forwarded twice.

usage: python run.py decider NAME [--theta 0.85]
"""
from __future__ import annotations

import argparse
import time

import redis

from pipeline import config, runtime
from pipeline.decisionlm_client import Permanent, Unreachable, call_decisionlm, decisionlm_healthy

# KEYS: dedupe key, target stream, source stream.  ARGV: group, msg id, maxlen, dedupe ttl, then field/value...
FORWARD_LUA = """
local fresh = redis.call('SET', KEYS[1], ARGV[2], 'NX', 'EX', ARGV[4])
if fresh then
  local args = {KEYS[2], 'MAXLEN', '~', ARGV[3], '*'}
  for i = 5, #ARGV do args[#args + 1] = ARGV[i] end
  redis.call('XADD', unpack(args))
end
redis.call('XACK', KEYS[3], ARGV[1], ARGV[2])
return fresh and 1 or 0
"""


class Decider:
    def __init__(self, r: redis.Redis, cfg: config.Config, name: str, theta: float | None = None):
        self.r, self.cfg, self.name = r, cfg, name
        self.theta = cfg.theta if theta is None else theta
        self._forward_script = r.register_script(FORWARD_LUA)
        self.done = 0
        self.last_maintenance = 0.0

    # --- what to write -----------------------------------------------------------------------------
    def target_stream(self, reply: dict) -> str:
        """The option's own stream, or review_stream when the reasoner failed and tier 1's answer was used."""
        if reply.get("low_confidence") and self.cfg.review_stream:
            return self.cfg.review_stream
        return self.cfg.option_streams[reply["answer"]]

    def build_output(self, record: dict, reply: dict) -> dict:
        """The original event plus the decision fields."""
        out = dict(record, label=reply["answer"], confidence=reply["confidence"], source=reply["source"],
                   tier1_label=reply["tier1_answer"], low_confidence=int(bool(reply.get("low_confidence"))),
                   context_truncated=int(reply["context_truncated"]), decision_latency_s=reply.get("latency_s", 0),
                   model_version=self.cfg.model_version, decided_at=time.time())
        if reply.get("reasoner_error"):
            out["reasoner_error"] = str(reply["reasoner_error"])[:300]
        return out

    def dedupe_key(self, record: dict, msg_id: str) -> tuple[str, int]:
        """Key and TTL for the 'already forwarded' marker. Without an event id, or with de-duplication off, the
        key is per delivery and so never collides."""
        event_id = record.get("id")
        if self.cfg.dedupe_ttl_s and event_id:
            return f"dedupe:{self.cfg.ingestion}:{event_id}", self.cfg.dedupe_ttl_s
        return f"dedupe:{self.cfg.ingestion}:msg:{msg_id}", 60

    def forward(self, msg_id: str, record: dict) -> None:
        reply = call_decisionlm(self.cfg, record, self.theta)
        out = self.build_output(record, reply)
        key, ttl = self.dedupe_key(record, msg_id)
        flat = [str(x) for kv in out.items() for x in kv]
        fresh = self._forward_script(keys=[key, self.target_stream(reply), self.cfg.ingestion],
                                     args=[self.cfg.ingestion_group, msg_id, self.cfg.maxlen, ttl, *flat])
        if not fresh:
            print(f"[{self.name}] duplicate event id {record.get('id')!r} ({msg_id}); already forwarded, acked", flush=True)

    def dead_letter(self, msg_id: str, record: dict, reason: str, attempts: int) -> None:
        pipe = self.r.pipeline(transaction=True)
        pipe.xadd(self.cfg.dlq, dict(record, source_id=msg_id, reason=reason[:300], attempts=attempts),
                  maxlen=self.cfg.maxlen, approximate=True)
        pipe.xack(self.cfg.ingestion, self.cfg.ingestion_group, msg_id)
        pipe.execute()
        print(f"[{self.name}] DLQ {msg_id}: {reason}", flush=True)

    # --- decisionLM availability -------------------------------------------------------------------
    def heartbeat(self, msg_id: str) -> None:
        """Reset the message's idle clock (JUSTID: no delivery counted) so it isn't reclaimed while we wait."""
        try:
            self.r.xclaim(self.cfg.ingestion, self.cfg.ingestion_group, self.name, 0, [msg_id], justid=True)
        except redis.RedisError:
            pass

    def wait_healthy(self, msg_id: str) -> bool:
        """Block until decisionLM answers /api/health. False if we were asked to stop first."""
        print(f"[{self.name}] waiting for decisionLM at {self.cfg.health_url}", flush=True)
        while not runtime.stop.is_set():
            if decisionlm_healthy(self.cfg):
                return True
            self.heartbeat(msg_id)
            runtime.stop.wait(self.cfg.health_poll_s)
        return False

    # --- one message -------------------------------------------------------------------------------
    def failed_attempts(self, msg_id: str) -> int | None:
        """Deliveries that already failed for a reclaimed message (the claim itself counted as one), or None if the
        message is no longer pending (someone else acknowledged it)."""
        info = self.r.xpending_range(self.cfg.ingestion, self.cfg.ingestion_group, min=msg_id, max=msg_id, count=1)
        return info[0]["times_delivered"] - 1 if info else None

    def handle(self, msg_id: str, record: dict, reclaimed: bool = False) -> None:
        """Fully process one message, or leave it pending (never acked unless forwarded or dead-lettered)."""
        attempts = 0
        if reclaimed:
            attempts = self.failed_attempts(msg_id)
            if attempts is None:
                return
            if attempts >= self.cfg.max_deliveries:
                return self.dead_letter(msg_id, record, f"gave up after {attempts} failed deliveries", attempts)
        self.try_forward(msg_id, record, attempts)

    def try_forward(self, msg_id: str, record: dict, attempts: int) -> None:
        """Forward with in-process retries. Permanent errors are dead-lettered; Redis errors propagate (run_loop
        handles outages); anything else leaves the message pending for redelivery."""
        delay = self.cfg.retry_backoff_s
        for attempt in range(self.cfg.retries + 1):
            try:
                return self.forward(msg_id, record)
            except Permanent as e:
                return self.dead_letter(msg_id, record, str(e), attempts + 1)
            except redis.RedisError:
                raise
            except Exception as e:  # Transient, or an unexpected bug: never lose the message
                print(f"[{self.name}] {msg_id} failed (try {attempt + 1}/{self.cfg.retries + 1}): {e}", flush=True)
                if runtime.stop.is_set():
                    return
                if isinstance(e, Unreachable):
                    if not self.wait_healthy(msg_id):
                        return
                elif attempt < self.cfg.retries:
                    runtime.stop.wait(delay)
                    delay *= 2
        print(f"[{self.name}] {msg_id} left pending for redelivery", flush=True)

    # --- loop --------------------------------------------------------------------------------------
    def warn_if_backlog_near_trim(self) -> None:
        for g in self.r.xinfo_groups(self.cfg.ingestion):
            lag = g.get("lag") if g["name"] == self.cfg.ingestion_group else None
            if lag and lag >= 0.8 * self.cfg.maxlen:  # XADD MAXLEN trims the oldest entries, even unread ones
                print(f"[{self.name}] WARNING: {lag} unread events vs maxlen {self.cfg.maxlen}; "
                      "the oldest will be trimmed before they are decided", flush=True)

    def maintenance(self) -> None:
        n = runtime.reclaim(self.r, self.cfg.ingestion, self.cfg.ingestion_group, self.name,
                            lambda mid, rec: self.handle(mid, rec, reclaimed=True), self.cfg.reclaim_idle_ms,
                            self.cfg.batch_size)
        if n:
            print(f"[{self.name}] reclaimed {n} message(s)", flush=True)
        self.warn_if_backlog_near_trim()

    def step(self) -> None:
        if time.time() - self.last_maintenance >= self.cfg.reclaim_every_s:
            self.maintenance()
            self.last_maintenance = time.time()
        resp = self.r.xreadgroup(self.cfg.ingestion_group, self.name, {self.cfg.ingestion: ">"},
                                 count=self.cfg.batch_size, block=self.cfg.block_ms)
        for _stream, msgs in resp or []:
            for i, (msg_id, record) in enumerate(msgs):
                if runtime.stop.is_set():
                    print(f"[{self.name}] {len(msgs) - i} fetched message(s) left pending for redelivery", flush=True)
                    return
                self.handle(msg_id, record)
                self.done += 1


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="consumer name (must be unique per running decider)")
    ap.add_argument("--theta", type=float, default=cfg.theta, help="escalation threshold sent to decisionLM")
    args = ap.parse_args(argv)
    if not 0 < args.theta <= 1:
        ap.error("--theta must be in (0, 1]")

    r = config.connect(cfg)
    runtime.install_signal_handlers(args.name)
    config.ensure_groups(r, cfg)
    print(f"[{args.name}] {cfg.ingestion} -> {cfg.url} theta={args.theta} options={cfg.option_names}", flush=True)
    d = Decider(r, cfg, args.name, args.theta)
    try:
        runtime.run_loop(r, cfg, d.step, args.name)
    except KeyboardInterrupt:
        pass
    print(f"[{args.name}] shut down after {d.done} messages", flush=True)


if __name__ == "__main__":
    main()
