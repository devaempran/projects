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
    def __init__(self, r, name, theta):
        self.r, self.name, self.theta = r, name, theta
        self._forward = r.register_script(FORWARD_LUA)
        self.done = 0
        self.last_maintenance = 0.0

    # --- one message -----------------------------------------------------------------------------
    def forward(self, msg_id, record):
        d = call_decisionlm(record, self.theta)
        if d.get("low_confidence") and config.REVIEW_STREAM:
            target = config.REVIEW_STREAM
        else:
            target = config.OPTION_STREAMS[d["answer"]]
        out = dict(record, label=d["answer"], confidence=d["confidence"], source=d["source"],
                   tier1_label=d["tier1_answer"], low_confidence=int(bool(d.get("low_confidence"))),
                   context_truncated=int(d["context_truncated"]), decision_latency_s=d.get("latency_s", 0),
                   model_version=config.MODEL_VERSION, decided_at=time.time())
        if d.get("reasoner_error"):
            out["reasoner_error"] = str(d["reasoner_error"])[:300]
        event_id = record.get("id")
        if config.DEDUPE_TTL_S and event_id:
            key, ttl = f"dedupe:{config.INGESTION}:{event_id}", config.DEDUPE_TTL_S
        else:  # de-duplication off, or no id to key on: a per-delivery key never collides
            key, ttl = f"dedupe:{config.INGESTION}:msg:{msg_id}", 60
        flat = [str(x) for kv in out.items() for x in kv]
        fresh = self._forward(keys=[key, target, config.INGESTION],
                              args=[config.INGESTION_GROUP, msg_id, config.MAXLEN, ttl, *flat])
        if not fresh:
            print(f"[{self.name}] duplicate event id {event_id!r} ({msg_id}); already forwarded, acked", flush=True)

    def dead_letter(self, msg_id, record, reason, attempts):
        pipe = self.r.pipeline(transaction=True)
        pipe.xadd(config.DLQ, dict(record, source_id=msg_id, reason=reason[:300], attempts=attempts),
                  maxlen=config.MAXLEN, approximate=True)
        pipe.xack(config.INGESTION, config.INGESTION_GROUP, msg_id)
        pipe.execute()
        print(f"[{self.name}] DLQ {msg_id}: {reason}", flush=True)

    def heartbeat(self, msg_id):
        """Reset the message's idle clock (JUSTID: no delivery counted) so it isn't reclaimed while we wait."""
        try:
            self.r.xclaim(config.INGESTION, config.INGESTION_GROUP, self.name, 0, [msg_id], justid=True)
        except redis.RedisError:
            pass

    def wait_healthy(self, msg_id):
        """Block until decisionLM answers /api/health. False if we were asked to stop first."""
        print(f"[{self.name}] waiting for decisionLM at {config.HEALTH_URL}", flush=True)
        while not runtime.stop.is_set():
            if decisionlm_healthy():
                return True
            self.heartbeat(msg_id)
            runtime.stop.wait(config.HEALTH_POLL_S)
        return False

    def handle(self, msg_id, record, reclaimed=False):
        """Fully process one message, or leave it pending (never acked unless forwarded or dead-lettered)."""
        attempts = 0
        if reclaimed:
            info = self.r.xpending_range(config.INGESTION, config.INGESTION_GROUP, min=msg_id, max=msg_id, count=1)
            if not info:
                return  # acked by someone else since it was claimed
            attempts = info[0]["times_delivered"] - 1  # the claim itself counted as a delivery
            if attempts >= config.MAX_DELIVERIES:
                return self.dead_letter(msg_id, record, f"gave up after {attempts} failed deliveries", attempts)
        delay = config.RETRY_BACKOFF_S
        for attempt in range(config.RETRIES + 1):
            try:
                return self.forward(msg_id, record)
            except Permanent as e:
                return self.dead_letter(msg_id, record, str(e), attempts + 1)
            except redis.RedisError:
                raise  # not a decisionLM problem; run_loop handles Redis outages
            except Exception as e:  # Transient, or an unexpected bug: never lose the message
                print(f"[{self.name}] {msg_id} failed (try {attempt + 1}/{config.RETRIES + 1}): {e}", flush=True)
                if runtime.stop.is_set():
                    return
                if isinstance(e, Unreachable):
                    if not self.wait_healthy(msg_id):
                        return
                elif attempt < config.RETRIES:
                    runtime.stop.wait(delay)
                    delay *= 2
        print(f"[{self.name}] {msg_id} left pending for redelivery", flush=True)

    # --- loop ------------------------------------------------------------------------------------
    def maintenance(self):
        n = runtime.reclaim(self.r, config.INGESTION, config.INGESTION_GROUP, self.name,
                            lambda mid, rec: self.handle(mid, rec, reclaimed=True), config.RECLAIM_IDLE_MS)
        if n:
            print(f"[{self.name}] reclaimed {n} message(s)", flush=True)
        for g in self.r.xinfo_groups(config.INGESTION):
            lag = g["name"] == config.INGESTION_GROUP and g.get("lag")
            if lag and lag >= 0.8 * config.MAXLEN:  # XADD MAXLEN trims the oldest, even unread, entries
                print(f"[{self.name}] WARNING: {lag} unread events vs maxlen {config.MAXLEN}; "
                      "the oldest will be trimmed before they are decided", flush=True)

    def step(self):
        if time.time() - self.last_maintenance >= config.RECLAIM_EVERY_S:
            self.maintenance()
            self.last_maintenance = time.time()
        resp = self.r.xreadgroup(config.INGESTION_GROUP, self.name, {config.INGESTION: ">"},
                                 count=config.BATCH, block=config.BLOCK_MS)
        for _stream, msgs in resp or []:
            for i, (msg_id, record) in enumerate(msgs):
                if runtime.stop.is_set():
                    print(f"[{self.name}] {len(msgs) - i} fetched message(s) left pending for redelivery", flush=True)
                    return
                self.handle(msg_id, record)
                self.done += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="consumer name (must be unique per running decider)")
    ap.add_argument("--theta", type=float, default=config.THETA, help="escalation threshold sent to decisionLM")
    args = ap.parse_args()
    if not 0 < args.theta <= 1:
        ap.error("--theta must be in (0, 1]")

    r = config.connect()
    runtime.install_signal_handlers(args.name)
    config.ensure_groups(r)
    print(f"[{args.name}] {config.INGESTION} -> {config.URL} theta={args.theta} options={config.OPTIONS}", flush=True)
    d = Decider(r, args.name, args.theta)
    try:
        runtime.run_loop(r, d.step, args.name)
    except KeyboardInterrupt:
        pass
    print(f"[{args.name}] shut down after {d.done} messages", flush=True)


if __name__ == "__main__":
    main()
