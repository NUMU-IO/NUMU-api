<!-- SPECKIT START -->
For additional context about technologies, project structure, schema changes,
contracts, and verification steps, read the current plan and its supporting
artifacts:

- Plan: [specs/backend-030-whatsapp-foundation/plan.md](specs/backend-030-whatsapp-foundation/plan.md)
- Spec: [specs/backend-030-whatsapp-foundation/spec.md](specs/backend-030-whatsapp-foundation/spec.md)
- Research: [specs/backend-030-whatsapp-foundation/research.md](specs/backend-030-whatsapp-foundation/research.md)
- Data model: [specs/backend-030-whatsapp-foundation/data-model.md](specs/backend-030-whatsapp-foundation/data-model.md)
- Contracts: [specs/backend-030-whatsapp-foundation/contracts/](specs/backend-030-whatsapp-foundation/contracts/)
- Quickstart: [specs/backend-030-whatsapp-foundation/quickstart.md](specs/backend-030-whatsapp-foundation/quickstart.md)
<!-- SPECKIT END -->

## Running tests

Use the project venv's interpreter explicitly. Anything below this line is
hand-maintained (speckit only rewrites the block above).

```bash
# Windows
.venv/Scripts/python.exe -m pytest tests/unit -q
.venv/Scripts/python.exe -m ruff check src/
.venv/Scripts/python.exe -m mypy src/

# macOS / Linux
.venv/bin/python -m pytest tests/unit -q
```

A bare `pytest`/`ruff`/`mypy` resolves against `PATH`, which on a dev box is
usually the *global* Python. If that interpreter holds a FastAPI/Starlette pair
outside FastAPI's declared range (`starlette>=0.40,<0.51`), importing
`tests/conftest.py` dies with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`
— thrown from inside FastAPI's `routing.py`, not from our code. It reads like a
broken suite or a dependency bug; it is neither. Re-run with the venv before
concluding anything about the tests.

`make test` / `make test-cov` intentionally call bare `pytest` so the same
targets work in Docker and CI (no `.venv` there) — they only hit the venv if it
is activated first. Prefer the explicit interpreter when running locally.
