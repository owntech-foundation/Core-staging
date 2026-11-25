# L0-FWK: framework self-test

Proves the campaign pipeline (runner, record, review page, control center) without hardware. Run it with `tests/run_all.sh --l0`.

| ID | Criterion | Verdict |
|---|---|---|
| L0-FWK-01 | `tests/catalogue.json` loads and is consistent (`tests/common/catalogue.py`) | PASS or FAIL |
| L0-FWK-02 | Host Python version, for the record | INFO |
| L0-FWK-03 | The review page's dependencies (numpy, matplotlib) are in `.venv` | PASS, or BLOCKED with the command that fixes it |
