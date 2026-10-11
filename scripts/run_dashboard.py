"""Start the API + embedded receiver (+ built dashboard if frontend/dist exists):  python scripts/run_dashboard.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, log_level="info")
