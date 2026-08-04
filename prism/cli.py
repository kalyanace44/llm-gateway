"""Prism CLI — `prism serve` starts the gateway."""
from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="prism", description="Prism — AI Traffic Control Plane")
    sub = parser.add_subparsers(dest="command")

    # serve
    serve_p = sub.add_parser("serve", help="Start the Prism gateway")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.add_argument("--host", type=str, default="0.0.0.0")
    serve_p.add_argument("--workers", type=int, default=4)
    serve_p.add_argument("--config", type=str, default="prism.yaml")
    serve_p.add_argument("--dev", action="store_true", help="Single worker, auto-reload")

    # version
    sub.add_parser("version", help="Print version")

    args = parser.parse_args()

    if args.command == "version":
        from prism import __version__
        print(f"prism {__version__}")

    elif args.command == "serve":
        import os
        os.environ.setdefault("PRISM_CONFIG", args.config)

        import uvicorn
        uvicorn.run(
            "prism.proxy.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            workers=1 if args.dev else args.workers,
            reload=args.dev,
            log_level="info",
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
