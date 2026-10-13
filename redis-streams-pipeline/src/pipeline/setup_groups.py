"""Create all streams and consumer groups from the config (safe to run repeatedly)."""
from __future__ import annotations

from pipeline import config


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    config.ensure_groups(config.connect(cfg), cfg)
    for s in [cfg.ingestion] + cfg.decision_streams:
        print(f"{s:28s} group={cfg.group_for(s)}")


if __name__ == "__main__":
    main()
