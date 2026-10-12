"""Start the decisionLM server:  python run.py      (or: PYTHONPATH=src python -m decisionlm)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from decisionlm.server import main  # noqa: E402

if __name__ == "__main__":
    main()
