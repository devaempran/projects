"""Print XLEN, XINFO GROUPS and pending counts for every stream, and the latest dead-letter reasons.
(Named inspect_streams.py rather than inspect.py: a local inspect.py would shadow Python's stdlib module.)"""
from __future__ import annotations

import redis

from pipeline import config


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    r = config.connect(cfg)
    try:
        r.ping()
    except redis.RedisError as e:
        raise SystemExit(f"cannot reach redis at {cfg.host}:{cfg.port}: {e}") from None
    for s in [cfg.ingestion, cfg.dlq] + cfg.decision_streams:
        print(f"{s}  XLEN={r.xlen(s)}")  # 0 for a stream that doesn't exist yet
        if s == cfg.dlq:
            for _id, rec in r.xrevrange(s, count=3):
                print(f"  latest: id={rec.get('id')} attempts={rec.get('attempts')} reason={rec.get('reason')}")
            continue
        try:
            groups = r.xinfo_groups(s)
        except redis.ResponseError:
            print("  (stream or groups missing; run `python run.py setup`)")
            continue
        for g in groups:
            print(f"  group {g['name']}: consumers={g['consumers']} pending={g['pending']} "
                  f"last-delivered={g['last-delivered-id']} lag={g.get('lag')}")
            if g["pending"]:
                for c in r.xpending(s, g["name"])["consumers"]:
                    print(f"    consumer {c['name']}: pending={c['pending']}")


if __name__ == "__main__":
    main()
