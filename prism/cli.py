"""Prism CLI — `prism serve` starts the gateway."""
from __future__ import annotations

import argparse
import sys

import yaml


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

    # validate
    validate_p = sub.add_parser("validate", help="Validate prism.yaml config")
    validate_p.add_argument("--config", type=str, default="prism.yaml")

    # version
    sub.add_parser("version", help="Print version")

    args = parser.parse_args()

    if args.command == "version":
        from prism import __version__
        print(f"prism {__version__}")

    elif args.command == "validate":
        _validate_config(args.config)

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


def _validate_config(path: str):
    """Validate a prism.yaml config file."""
    import os

    if not os.path.exists(path):
        print(f"✗ Config file not found: {path}")
        sys.exit(1)

    try:
        from prism.config import PrismConfig
        config = PrismConfig.from_yaml(path)
    except (yaml.YAMLError, TypeError, KeyError, ValueError, OSError) as e:
        print(f"✗ Config parse error: {e}")
        sys.exit(1)

    errors = []
    warnings = []

    # Check providers
    if not config.providers:
        errors.append("No providers configured")
    else:
        for p in config.providers:
            if not p.base_url:
                errors.append(f"Provider '{p.name}': missing base_url")
            if not p.api_key and "${" not in (p.api_key or ""):
                warnings.append(f"Provider '{p.name}': api_key is empty (ok if set via env var)")
            if not p.models:
                warnings.append(f"Provider '{p.name}': no models listed (will accept any model)")

    # Check admin key
    if not config.admin_key:
        warnings.append("No admin_key set — admin endpoints will be unprotected")

    # Check cache
    if config.cache.enabled and config.cache.semantic_threshold < 0.8:
        warnings.append(f"Semantic cache threshold {config.cache.semantic_threshold} is very low — may return incorrect cached responses")

    # Check resilience
    if config.resilience.failure_threshold < 2:
        warnings.append("Circuit breaker failure_threshold < 2 — may trip too aggressively")

    # Print results
    print(f"Config: {path}")
    print(f"Providers: {len(config.providers)} ({', '.join(p.name for p in config.providers)})")
    print(f"Port: {config.port}")
    print(f"Cache: {'enabled' if config.cache.enabled else 'disabled'} (TTL: {config.cache.ttl_seconds}s, semantic: {config.cache.semantic_threshold})")
    print(f"Resilience: threshold={config.resilience.failure_threshold}, recovery={config.resilience.recovery_timeout_seconds}s")
    print()

    if errors:
        for e in errors:
            print(f"  ✗ ERROR: {e}")
        print()
        print("Config is INVALID — fix errors above.")
        sys.exit(1)

    if warnings:
        for w in warnings:
            print(f"  ⚠ WARNING: {w}")
        print()

    print("✓ Config is valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
