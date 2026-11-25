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

## What is tested

The campaign runs on `main` with the PR's reviewed head SHA merged onto it, which is the code that merging would
give. A PR branched from Core, which has no `tests/`, therefore still runs the framework, and a PR's own new tests
run with it. A PR that does not merge cleanly onto `main` fails at the merge step: rebase it. The record names the
local merge commit, and the run summary gives the PR head and the `main` commit it was merged onto.

## What a campaign does to the bench

- **Before and after**: [`tests/common/bench_restore.sh`](../common/bench_restore.sh), taken from `main`, uploads
  Core-CUT to the CUT over the ST-Link. The CUT is then at rest, and the SPOC tests can drive it.
- **Order**: `tests/run_all.sh` runs the L0 tests alphabetically, except that tests carrying a `## @flashes-cut`
  line in their `run.sh` run last. **A new test that reflashes the CUT must carry that line.** Otherwise the
  SPOC tests after it find the CUT off Core-CUT.

## Sharing the bench

The GitHub `concurrency` group only orders GitHub jobs. People and assistant sessions on the bench computer use
it too. So every bench user takes a **host-wide lock**,
[`tests/common/bench_lock.sh`](../common/bench_lock.sh) (`/run/lock/spoc-bench.lock`). `run_all.sh --target`,
`bench_restore.sh` and `spoc_cal/run.sh --run` take it and wait while it is held. `/run/lock/spoc-bench.lock.who`
says who holds it. A script that flashes or drives the bench outside these entry points (a bare
`pio run -t upload`, for example) must take it too:

```sh
. tests/common/bench_lock.sh && bench_lock "what I am doing"; pio run -e <env> -t upload
```

The first campaign run for this README showed why. A session flashing the CUT from another checkout took the
ST-Link in the middle of the campaign. L0-TGT could not open the probe, and L0-CAL read CH2–4 as invalid.

## Host setup (once per bench computer)

```sh
sudo RUNNER_TOKEN=$(gh api -X POST repos/owntech-foundation/Core-staging/actions/runners/registration-token --jq .token) \
     SPOC_SOFTWARE_SRC=$HOME/code/SPOC_Software bash tests/runner/install.sh
```

The runner's files (PlatformIO toolchains, Core-CUT, job builds) take over 12 GB. They live in `/home/spoc-runner`
(`SPOC_RUNNER_DATA`), with `/var/lib/spoc-runner` a symlink to it, and the script refuses to start with less than
15 GB free there. On a laptop with a small root partition, the first install put them on `/` and filled it.

Variables go after `sudo`, because sudo drops the ones set before it. The registration token is valid for one hour.
The script re-applies the udev rules to the bench's USB devices only. Never run a bare `udevadm trigger`: it
replays every device on the computer and can restart the desktop session.

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

**Working on the bench by hand**: take the lock (above), or stop the service
(`sudo /var/lib/spoc-runner/actions-runner/svc.sh stop`) and start it again afterwards. Then run
`SPOC_CUT_DIR=/var/lib/spoc-runner/Core-CUT bash tests/common/bench_restore.sh` if you reflashed the CUT.

## Limits

- The runner directory is writable by `spoc-runner`, because the runner must be able to update itself. A job that
  runs can therefore change the runner for later jobs. The hook and `.env` are root-owned, but the hook only stops
  workflows that nobody reviewed. Every job that runs has been reviewed and approved, and that review is the
  real gate.
- On a personal laptop a job can read world-readable files and reach the LAN. The dedicated bench (Raspberry Pi)
  removes that: it needs the `linux-arm64` runner and a new registration, and nothing else changes.
- The runner must stay within 63 days of the latest release, and it updates itself.
