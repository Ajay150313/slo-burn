# Contributing to slo-burn

Thanks for taking the time. Patches, bug reports, and documentation fixes are all welcome.

## Dev setup

```bash
git clone https://github.com/Ajay150313/slo-burn.git
cd slo-burn
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Running the tests

```bash
pytest tests/ -v
```

For a single test file:

```bash
pytest tests/test_slo_burn.py -v -k "test_burn_rate"
```

## Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/core/ src/exporters/ src/rules/
```

CI runs all of these. Your PR will fail if linting fails.

## Submitting a pull request

1. Fork the repo
2. Create a branch: `git checkout -b feat/my-thing`
3. Make your changes with tests
4. Run `pytest` and `ruff check` locally before pushing
5. Open a PR against `main` with a clear description of *why*, not just *what*

PRs without tests for new behaviour will be asked to add them before merge.

## What we need most

These are tagged [`good first issue`](https://github.com/Ajay150313/slo-burn/issues?q=label%3Agood-first-issue):

- **New Relic backend** — follow `src/exporters/prometheus.py`, implement the `MetricsBackend` protocol
- **OpenTelemetry / OTLP backend** — same pattern
- **Helm chart** — basic Kubernetes deployment
- **Email notification target** — SMTP-based, follow `src/rules/alerter.py`
- **More SLO templates** — follow `src/rules/templates.py`

## Questions

Open a GitHub Discussion, not an Issue, for design questions or feature ideas that aren't yet concrete.

## License

By contributing you agree that your contributions will be licensed under the Apache 2.0 License.
