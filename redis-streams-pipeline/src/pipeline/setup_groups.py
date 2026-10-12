"""Create all streams and consumer groups from the config (safe to run repeatedly)."""
from pipeline import config


def main():
    r = config.connect()
    config.ensure_groups(r)
    for s in [config.INGESTION] + config.DECISION_STREAMS:
        print(f"{s:28s} group={config.group_for(s)}")


if __name__ == "__main__":
    main()
