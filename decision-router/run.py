"""Run the whole decision-router from the project root:  python run.py start [--config PATH]
(the same as `python pipeline/run.py ...`; see pipeline/README.md for the other commands)"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pipeline", "src"))

from pipeline.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
