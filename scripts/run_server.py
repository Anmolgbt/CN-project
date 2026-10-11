"""Start only the TCP receiver:  python scripts/run_server.py --port 9000"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
