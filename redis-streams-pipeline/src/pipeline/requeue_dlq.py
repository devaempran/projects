"""Move dead-lettered events back onto the ingestion stream (e.g. after an outage or a config fix).
Each is re-added with its original fields (the dead-letter fields are stripped) and removed from the DLQ.
usage: python run.py requeue [--reason SUBSTRING] [--limit N] [--dry-run]"""
import argparse

from pipeline import config

DLQ_FIELDS = {"source_id", "reason", "attempts"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="", help="only events whose reason contains this text")
    ap.add_argument("--limit", type=int, default=0, help="at most this many (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    r = config.connect()
    moved = 0
    for dlq_id, rec in r.xrange(config.DLQ):
        if args.reason and args.reason not in rec.get("reason", ""):
            continue
        if args.limit and moved >= args.limit:
            break
        if not args.dry_run:
            pipe = r.pipeline(transaction=True)  # re-add and remove together
            pipe.xadd(config.INGESTION, {k: v for k, v in rec.items() if k not in DLQ_FIELDS},
                      maxlen=config.MAXLEN, approximate=True)
            pipe.xdel(config.DLQ, dlq_id)
            pipe.execute()
        moved += 1
    print(f"{'would requeue' if args.dry_run else 'requeued'} {moved} event(s); {r.xlen(config.DLQ)} left in {config.DLQ}")


if __name__ == "__main__":
    main()
