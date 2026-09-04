from __future__ import annotations

import argparse
import os

import uvicorn

from tides_pool.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tides-pool", description="DATUM-only TIDES mining pool"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_serve_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default=None)
        p.add_argument("--port", type=int, default=None)
        p.add_argument(
            "--role",
            default=None,
            choices=["all", "web", "prime"],
            help="Override TIDES_ROLE (all|web|prime)",
        )

    serve = sub.add_parser(
        "serve",
        help="Run HTTP API (and Prime when role=all/prime)",
    )
    _add_serve_args(serve)

    serve_web = sub.add_parser(
        "serve-web",
        help="Website/API only — does not start DATUM Prime",
    )
    _add_serve_args(serve_web)

    serve_prime = sub.add_parser(
        "serve-prime",
        help="DATUM Prime + chain sync (minimal HTTP for health)",
    )
    _add_serve_args(serve_prime)

    args = parser.parse_args()

    role_override = getattr(args, "role", None)
    if args.cmd == "serve-web":
        role_override = role_override or "web"
    elif args.cmd == "serve-prime":
        role_override = role_override or "prime"

    if role_override:
        os.environ["TIDES_ROLE"] = role_override

    settings = Settings()
    if args.cmd in ("serve", "serve-web", "serve-prime"):
        host = args.host or settings.host
        port = args.port or settings.port
        uvicorn.run("tides_pool.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
