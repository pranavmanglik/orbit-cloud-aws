# orbit-cloud-aws: development

The supported Python range is declared in `pyproject.toml`. Local checks do not establish hosted CI or cloud-service compatibility.

```bash
python -m pip install -e '.[dev]'
pytest
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

Provider tests use fake SDK clients and do not require cloud credentials. Live inventory checks must be separately opted in and run against a dedicated non-production account/project/subscription.

The current local quality matrix passes on CPython 3.11, 3.12, 3.13, and 3.14: all 10 tests, Ruff
lint and format checks, strict mypy, and wheel/sdist builds pass on each interpreter. These checks
use a fake Resource Explorer client; they do not verify AWS permissions, service availability, or
hosted CI.
