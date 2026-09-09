#!/usr/bin/env python3
"""Start the private web API; the evaluator runs in its existing Python env."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from av_eval.web_api import create_app
from av_eval.web_jobs import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--max-upload-mib", type=int, default=128)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local/web-jobs")
    parser.add_argument("--runner-python", type=Path, default=os.getenv("AVAGENT_WEB_RUNNER_PYTHON", sys.executable))
    parser.add_argument("--origin", action="append", default=["https://xuanhaochang.github.io"])
    args = parser.parse_args()
    if not 1 <= args.max_upload_mib <= 128:
        parser.error("--max-upload-mib must be between 1 and 128")
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = data / "access-token"
    if not token_path.exists():
        with open(token_path, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            stream.write(secrets.token_urlsafe(32))
    settings = Settings(repo=args.repo.resolve(), data=data, python=args.runner_python.absolute(),
                        token=token_path.read_text().strip(), origins=tuple(args.origin),
                        max_upload_bytes=args.max_upload_mib * 1024 * 1024)
    missing = settings.readiness()
    print(f"avagent-eval API: http://{args.host}:{args.port}", flush=True)
    print(f"Private browser access token file: {token_path} (not the model API key)", flush=True)
    if missing:
        print("Evaluation disabled until configured: " + ", ".join(missing), flush=True)
    import uvicorn
    uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
