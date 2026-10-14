"""Command dispatcher:  python run.py <command> [args]   (or PYTHONPATH=src python -m pipeline <command> [args])

  start                 run everything: check config, create streams, start decisionLM and the deciders, keep them alive
  dashboard             serve the live dashboard on its own   (--host H --port P)
  setup                 create streams and consumer groups from config/pipeline.toml
  producer              publish test events           (--count N --rate R)
  decider NAME          decide ingestion events       (--theta T)
  worker STREAM [NAME]  consume one decision stream
  inspect               show stream lengths, groups, pending counts, latest dead letters
  requeue               move dead-lettered events back to ingestion   (--reason TEXT --limit N --dry-run)
"""
import importlib
import sys

COMMANDS = {"start": "supervisor", "dashboard": "dashboard.server", "setup": "setup_groups", "producer": "producer",
            "decider": "decider", "worker": "worker", "inspect": "inspect_streams", "requeue": "requeue_dlq"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        raise SystemExit(0 if not argv or argv[0] in ("-h", "--help") else 2)
    sys.argv = [f"pipeline {argv[0]}"] + argv[1:]
    importlib.import_module(f"pipeline.{COMMANDS[argv[0]]}").main()


if __name__ == "__main__":
    main()
