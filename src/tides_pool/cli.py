from __future__ import annotations

import argparse

import uvicorn

from tides_pool.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="tides-pool", description="DATUM-only TIDES mining pool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    args = parser.parse_args()
    settings = Settings()

    if args.cmd == "serve":
        host = args.host or settings.host
        port = args.port or settings.port
        uvicorn.run("tides_pool.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
