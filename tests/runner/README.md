# SPOC bench runner

A self-hosted GitHub Actions runner on the computer wired to the SPOC bench (CUT Spin, MUX Spin, ST-Link, MHO984).
It runs one workflow, [`.github/workflows/spoc.yml`](../../.github/workflows/spoc.yml) from `main`. That workflow runs a
test campaign on the exact commit of a pull request that a maintainer has reviewed. Design and sources:
[Core-staging#4](https://github.com/owntech-foundation/Core-staging/issues/4).

## Running the bench on a pull request

1. Read the whole diff. Firmware is not the only thing that runs: building runs PlatformIO scripts and CMake, and
   the campaign runs `tests/` shell and Python, all on the bench computer.
2. Add the label **`spoc-run`** to the PR, or dispatch it with the SHA you reviewed:
   ```sh
   gh workflow run spoc.yml -R owntech-foundation/Core-staging -f pr=<N> -f sha=<full head SHA>
   ```
3. The job waits on the environment **`spoc-bench`**. Approve it from the run page (required reviewer: Luiz).
4. The record appears on the run page (job summary). The campaign folder, with `report.html` and its figures, is
   in the run's artifacts. A comment on the PR gives the verdict and is updated on every run. The label is then
   removed, so a later push needs a fresh review and label.

Campaigns queue: one runs at a time and none is cancelled. If the computer is asleep or the service is stopped,
the job waits.

## What a campaign does to the bench

- **Before and after**: [`tests/common/bench_restore.sh`](../common/bench_restore.sh), taken from `main`, uploads
  Core-CUT to the CUT over the ST-Link. The CUT is then at rest, and the SPOC tests can drive it.
- **Order**: `tests/run_all.sh` runs the L0 tests alphabetically, except that tests carrying a `## @flashes-cut`
  line in their `run.sh` run last. **A new test that reflashes the CUT must carry that line.** Otherwise the
  SPOC tests after it find the CUT off Core-CUT.

## Host setup (once per bench computer)

```sh
RUNNER_TOKEN=$(gh api -X POST repos/owntech-foundation/Core-staging/actions/runners/registration-token --jq .token) \
SPOC_SOFTWARE_SRC=~/code/SPOC_Software \
sudo -E bash tests/runner/install.sh
```

[`install.sh`](install.sh) can be run again safely. It sets up:

| Item | Where |
|---|---|
| user `spoc-runner`: groups `plugdev` and `dialout`, no `sudo` and no `docker` (both give root) | home `/var/lib/spoc-runner` |
| udev rules: group access and stable names `/dev/spoc-scope`, `/dev/spoc-stlink`, `/dev/spoc-cut`, `/dev/spoc-mux`, keyed on serial numbers | [`99-spoc-bench.rules`](99-spoc-bench.rules) → `/etc/udev/rules.d/` |
| pre-job hook: fails every job that is not `spoc.yml@refs/heads/main` on `pull_request_target` or `workflow_dispatch` | [`job_started.sh`](job_started.sh) → `/usr/local/lib/spoc-runner/`, root-owned |
| PlatformIO, the test venv, a copy of Core-CUT (branch `Core-CUT` of the private SPOC_Software repository, cloned from a local checkout, with no credentials) | `~spoc-runner/.platformio`, `venv`, `Core-CUT` |
| the runner (label `spoc`), its `.env` (hook, `SPOC_CUT_DIR`, `SPOC_VENV`, `RIGOL_USBTMC`), the systemd service | `~spoc-runner/actions-runner` |

The serial numbers in `99-spoc-bench.rules` are those of the bench on 2026-11-25. Edit them when a board or probe is
swapped. PlatformIO's own `99-platformio-udev.rules` still opens the Spins to everyone (`MODE:=0666`). Tightening
that is left for the dedicated bench.

Settings on the repository (already applied on Core-staging): fork PRs need approval for **all outside contributors**,
the environment `spoc-bench` (required reviewer Luiz, administrators cannot bypass, branch `main` only) and the
label `spoc-run`.

**Working on the bench by hand**: stop the service first (`sudo /var/lib/spoc-runner/actions-runner/svc.sh stop`),
otherwise a queued campaign may take the ST-Link from you. Start it again afterwards, then run
`SPOC_CUT_DIR=/var/lib/spoc-runner/Core-CUT bash tests/common/bench_restore.sh` if you reflashed the CUT.

## Limits

- The runner directory is writable by `spoc-runner`, because the runner must be able to update itself. A job that
  runs can therefore change the runner for later jobs. The hook and `.env` are root-owned, but the hook only stops
  workflows that nobody reviewed. Every job that runs has been reviewed and approved, and that review is the
  real gate.
- On a personal laptop a job can read world-readable files and reach the LAN. The dedicated bench (Raspberry Pi)
  removes that: it needs the `linux-arm64` runner and a new registration, and nothing else changes.
- The runner must stay within 63 days of the latest release, and it updates itself.
