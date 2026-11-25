# Tests

Board-agnostic tests of the OwnTech Core. Every test is defined once in [`catalogue.json`](catalogue.json); results come only from campaigns under `tests/out/`, and `tests/out/control-center.html` shows where the validation stands. Terms (campaign, gate, graduation…) are defined in the [glossary](../GLOSSARY.md).

Application-specific tests live on their application branches; this folder holds what every board shares.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt   # once: the review page needs numpy and matplotlib
```

Without `.venv`, a campaign still writes `record.md` and the control center; only the review page is skipped.

## Run

```bash
tests/run_all.sh --l0     # every tests/l0/*/run.sh
tests/run_all.sh          # L0, then tests/l1/run.sh when there is one
```

Each campaign writes `tests/out/<date>_<sha>_<time>_<id>/` with one log per test script, `record.md` (verdicts), `report.html` (the review page) and updates `tests/out/control-center.html`. The runner exits 1 on any FAIL, or when no criterion was reported.

## Layout

| Path | Content |
|---|---|
| [`run_all.sh`](run_all.sh) | Campaign runner: every `tests/l0/*/run.sh`, then record, review page, control center |
| [`common/`](common/README.md) | Check helper, record writer, review page, control center, catalogue checker |
| [`l0/framework_selftest/`](l0/framework_selftest/README.md) | L0-FWK: the framework checks itself, without hardware |
| [`catalogue.json`](catalogue.json) | Every test, defined once |
| [`requirements.txt`](requirements.txt) | Python packages of the review page |
| `out/` | Campaigns and `control-center.html` (git-ignored) |

## Adding a test

1. Add it to `catalogue.json` (tab, level, title, criterion, conditions, source, status) and run `python3 tests/common/catalogue.py`. IDs look like `L0-ABC-01`.
2. Write it in `tests/l0/<name>/` with a `run.sh` and a `README.md`; add its object code to `object_code()` in `run_all.sh`. Each criterion prints one result line: `@@<TAB>ID<TAB>PASS|FAIL|INFO|BLOCKED<TAB>what<TAB>expected<TAB>measured`.
3. **Results reach the control center only from a campaign under `tests/out/`**: `run_all.sh`, or a `run.sh` that writes a campaign of its own. Never report results into a scratch folder.
4. **Done means visible**: run it through a campaign, open `tests/out/control-center.html`, check the test shows its newest result and its link opens the review page.
