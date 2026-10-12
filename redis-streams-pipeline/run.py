"""Pipeline entry point:  python run.py <command> [args]     (python run.py --help lists the commands)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from pipeline.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
