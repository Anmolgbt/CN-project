"""Send one file from the command line:  python scripts/run_client.py FILE --connections 4"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
