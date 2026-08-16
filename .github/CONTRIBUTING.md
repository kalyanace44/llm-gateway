# Contributing to Prism

Thanks for your interest in contributing to Prism! This guide covers how to set up your environment, make changes, and submit them for review.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Docker (for integration tests)
- Helm 3 (for chart changes)

### Setup

```bash
git clone https://github.com/kalyanace44/llm-gateway.git
cd llm-gateway
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Development Workflow

1. **Fork and branch** — create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```

2. **Make your changes** — follow existing code patterns and conventions.

3. **Lint** — we use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:
   ```bash
   ruff check .
   ruff format .
   ```

4. **Test** — run the test suite with pytest:
   ```bash
   pytest
   pytest --cov=prism  # with coverage
   ```

5. **Commit** — use [Conventional Commits](https://www.conventionalcommits.org/):
   ```
   feat: add retry logic to bedrock provider
   fix: handle empty response from cache layer
   docs: update helm values reference
   ```

6. **Push and open a PR** against `main`.

## Code Style

- Ruff handles formatting and import sorting — no need for black or isort separately.
- Type hints are required for all public functions.
- Docstrings follow Google style.

## Testing

- Unit tests go in `tests/unit/`, integration tests in `tests/integration/`.
- New features require tests. Bug fixes should include a regression test.
- Integration tests that need external services should be marked with `@pytest.mark.integration`.

## Helm Chart

Chart sources live in `charts/prism/`. When modifying the chart:

```bash
helm lint charts/prism
helm template prism charts/prism --values charts/prism/values.yaml
```

Bump the chart version in `Chart.yaml` if you change templates or defaults.

## Pull Request Guidelines

- Keep PRs focused — one logical change per PR.
- Include a description of what changed and why.
- Ensure CI passes (lint, tests, helm lint).
- Maintainers may request changes; please respond within a reasonable timeframe.

## Reporting Issues

Open an issue on GitHub with:
- Steps to reproduce
- Expected vs. actual behavior
- Environment details (OS, Python version, deployment method)

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](../LICENSE).
