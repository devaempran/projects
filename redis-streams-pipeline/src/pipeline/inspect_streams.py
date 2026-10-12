"""Print XLEN, XINFO GROUPS and pending counts for every stream, and the latest dead-letter reasons.
(Named inspect_streams.py rather than inspect.py: a local inspect.py would shadow Python's stdlib module.)"""
import redis

from pipeline import config


def main():
    r = config.connect()
    try:
        r.ping()
    except redis.RedisError as e:
        raise SystemExit(f"cannot reach redis at {config.HOST}:{config.PORT}: {e}")
    for s in [config.INGESTION, config.DLQ] + config.DECISION_STREAMS:
        print(f"{s}  XLEN={r.xlen(s)}")  # 0 for a stream that doesn't exist yet
        if s == config.DLQ:
            for _id, rec in r.xrevrange(s, count=3):
                print(f"  latest: id={rec.get('id')} attempts={rec.get('attempts')} reason={rec.get('reason')}")
            continue
        try:
            groups = r.xinfo_groups(s)
        except redis.ResponseError:
            print("  (stream or groups missing; run setup_groups.py)")
            continue
        for g in groups:
            print(f"  group {g['name']}: consumers={g['consumers']} pending={g['pending']} "
                  f"last-delivered={g['last-delivered-id']} lag={g.get('lag')}")
            if g["pending"]:
                for c in r.xpending(s, g["name"])["consumers"]:
                    print(f"    consumer {c['name']}: pending={c['pending']}")


if __name__ == "__main__":
    main()
