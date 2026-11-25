# A self-hosted GitHub runner driving the SPOC bench

Research for [Core-staging#4](https://github.com/owntech-foundation/Core-staging/issues/4). Date: 2026-11-25.
Facts are cited to GitHub's own docs; anything marked **unverified** was not confirmed in a primary source.

## Short answer

Never let a `pull_request` workflow reach the bench. The bench runs only from a **trusted workflow on `main`**, started by a maintainer after reading the diff: `pull_request_target` with `types: [labeled]` (label `spoc-run`), or `workflow_dispatch` with a PR number. The job then waits on an **environment `spoc-bench` with Luiz as required reviewer**. It checks out the **exact PR head SHA** the maintainer reviewed, holds a read-only token and no secrets, and runs under a **dedicated `spoc-runner` Unix user**. That user is not in `sudo` or `docker`, and the hardware reaches it through udev rules. One runner plus a `concurrency` group with `queue: max` serialises the campaigns. The campaign folder goes up as an artifact and `record.md` becomes the job summary. A separate job on a GitHub-hosted runner posts a short PR comment. Everything except the runner tarball (x64 → arm64) and the per-machine registration carries over to a Raspberry Pi unchanged.

## 1. Facts that drive the design

### Self-hosted runners on a public repo
- GitHub: self-hosted runners "should almost never be used for public repositories", because "any user can open pull requests against the repository and compromise the environment". They "can be persistently compromised by untrusted code in a workflow". [Security hardening, self-hosted runners](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- "There is no way to guarantee that a self-hosted runner only runs one job". JIT runners "perform at most one job", but "re-using hardware to host JIT runners can risk exposing information from the environment." (same page)
- The runner needs only outbound HTTPS on port 443, so no inbound port has to be opened on the laptop. [Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)

### How fork PRs can reach a runner
- With `pull_request`, the workflow comes from the PR's merge ref (`GITHUB_REF` = `refs/pull/<n>/merge`). [Variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables) A fork PR can therefore **edit or add a workflow** with `runs-on: [self-hosted, spoc]`. Forks get no secrets and a read-only `GITHUB_TOKEN`. [Events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) That does not stop code from running on the runner host.
- The only gate on such runs is the fork-approval setting. Its options: *first-time contributors who are new to GitHub*, *first-time contributors*, or *all external contributors* (anyone who is not a repo member or owner and not an org member). [Managing Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- **Core-staging today is `first_time_contributors`** (`GET /repos/.../actions/permissions/fork-pr-contributor-approval`). After one merged PR, a contributor's later fork PRs run without approval, and those runs could target the bench. Change it to **all external contributors** before registering a runner.
- `pull_request_target` workflows "will always run, regardless of approval settings". (same page) They run "in the context of the default branch of the base repository", so a fork **cannot change the workflow file**. They are privileged, though: GitHub warns against checking out untrusted PR code in them. [Events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [Hardening](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- `workflow_dispatch` requires write access, and the workflow must be on the default branch. [Manually run a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- Adding a label needs triage access, so `labeled` is maintainer-only. The `labeled` activity type exists for `pull_request` and `pull_request_target`. [Events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

### Environments
- Required reviewers: up to six users or teams, and one approval suffices. Self-review can be blocked; for a sole maintainer, leave it allowed. Administrators bypass by default, which can be disabled. Branch/tag deployment restrictions are available. On Free, Pro and Team plans, required reviewers work **only for public repositories**, which is our case: the owntech-foundation org is on the Free plan. [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)

### Runner groups and workflow restriction
- Runner groups are off-limits to public repositories by default ("By default, only private repositories can access runners in a runner group"). GitHub recommends self-hosted runners for private repositories only. [Manage access](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/manage-access)
- A runner group can be limited to **selected workflows** pinned by full path and ref, e.g. `owntech-foundation/Core-staging/.github/workflows/spoc.yml@refs/heads/main`. [Manage access (GHEC)](https://docs.github.com/en/enterprise-cloud@latest/actions/how-tos/manage-runners/self-hosted-runners/manage-access), [changelog](https://github.blog/changelog/2022-03-21-github-actions-restrict-self-hosted-runner-groups-to-specific-workflows) **Unverified:** whether a Free-plan org can set this on its Default group. Extra groups need Team. Check under Org settings → Actions → Runner groups → Default. If it is available, it is the strongest control: a fork's own `pull_request` workflow can never get the runner.
- **Fallback that works on any plan:** a runner **pre-job hook** (`ACTIONS_RUNNER_HOOK_JOB_STARTED`, set in the runner's `.env`). If the hook exits non-zero, "the job will not run and will be marked as failed". The hook sees the default variables, including `GITHUB_EVENT_NAME` and `GITHUB_WORKFLOW_REF`. It must live outside the runner directory. [Run scripts](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/run-scripts) Have it refuse everything except `GITHUB_WORKFLOW_REF == owntech-foundation/Core-staging/.github/workflows/spoc.yml@refs/heads/main`. Make the script root-owned and not writable by the runner user. The hook has no timeout setting.

### Concurrency
- With a `concurrency` group there is at most one running job. The default `queue: single` **cancels and replaces** an existing pending run. `queue: max` keeps up to 100 pending runs. `queue: max` cannot be combined with `cancel-in-progress: true`. [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- `permissions:` with any key set leaves every unspecified scope at `none`. (same page)

### Reporting
- Job summary (`$GITHUB_STEP_SUMMARY`): GitHub-flavoured Markdown, max 1 MiB per step, 20 summaries per job. [Workflow commands](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands)
- With `actions/upload-artifact@v7` and `archive: false`, a **single file** can be uploaded unzipped and opened in the browser. Simple HTML works, but its relative CSS, JS and image links do not. [Changelog 2026-02-26](https://github.blog/changelog/2026-02-26-github-actions-now-supports-uploading-and-downloading-non-zipped-artifacts/) Our `report.html` links relative `screen.png` and `model_power.png` files (`tests/common/review_report.py`), so a lone unzipped `report.html` shows no figures. Upload the whole campaign folder zipped, plus optionally the unzipped `record.md`.
- Pages: one site per repository, at most 1 GB, and deploys over 10 minutes time out. [Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) A Pages site of a public repo is public. That suits a cumulative control center, but it is not per-PR.

### Running the runner
- Install as a systemd service: `sudo ./svc.sh install <user>`, then `sudo ./svc.sh start` and `sudo ./svc.sh status`. [Configure the application](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application)
- Linux ARM64 is a supported architecture. [Reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners) Current release v2.338.2 ships `actions-runner-linux-arm64-2.338.2.tar.gz`. [actions/runner releases](https://github.com/actions/runner/releases)
- The runner auto-updates, and a runner must be updated within 63 days of a newer release. Auto-update can be disabled. [About self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners), [Reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)

## 2. Recommended design

### Trigger and approval
`.github/workflows/spoc.yml` on `main`:

```yaml
on:
  pull_request_target:
    types: [labeled]
  workflow_dispatch:
    inputs:
      pr:  { description: PR number, required: true }
      sha: { description: exact head SHA you reviewed, required: true }
      campaign: { description: run_all.sh flags, default: "--l0 --target" }

permissions: {}               # nothing by default

jobs:
  bench:
    if: github.event_name == 'workflow_dispatch' || github.event.label.name == 'spoc-run'
    runs-on: [self-hosted, linux, spoc]      # no X64/ARM64 label, so the Pi drops in
    environment: spoc-bench                  # required reviewer: Luiz
    permissions: { contents: read }
    timeout-minutes: 90
    concurrency: { group: spoc-bench, queue: max }
    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ github.event.pull_request.head.sha || inputs.sha }}   # the reviewed SHA, never a branch name
          persist-credentials: false
      - run: tests/run_all.sh ${{ inputs.campaign || '--l0 --target' }}  # in practice pass via env, not inline
      - run: cat tests/out/*/record.md >> "$GITHUB_STEP_SUMMARY"
        if: always()
      - uses: actions/upload-artifact@v7
        if: always()
        with: { name: spoc-campaign, path: tests/out/ }
      - run: tests/bench_safe_state.sh   # reflash env USB / power down; to be written
        if: always()
  comment:
    needs: bench
    if: always() && github.event_name == 'pull_request_target'
    runs-on: ubuntu-latest           # the write token never touches the bench
    permissions: { pull-requests: write }
    steps: [ ...post verdict table + link to the run... ]
```

Flow: a maintainer reads the diff, then adds `spoc-run`, or runs `gh workflow run spoc.yml -f pr=N -f sha=…`. The job waits on `spoc-bench`, Luiz approves it, and the bench runs that SHA. Remove the label after the run, so a re-run needs a fresh label after the next review. **Building is executing.** PlatformIO `extra_scripts`, CMake, `tests/*.sh` and the Python helpers from the PR all run on the host. Review covers those files too, not only firmware.

Repo settings (Luiz to do, not done here):
1. Set fork approval to **all external contributors**.
2. Create the environment `spoc-bench`: required reviewer Luiz, administrators cannot bypass, deployment branch restricted to `main`.
3. Restrict the runner to `spoc.yml@refs/heads/main` through runner-group workflow access if the Free plan allows it, **and** install the pre-job hook regardless.
4. Create the label `spoc-run`.

### Runner host and USB access
- Create a dedicated user `spoc-runner`: no `sudo` and **not in `docker`** (docker group membership is root). Its home and runner directory sit outside `/home/luiz-villa`. Install the service with `sudo ./svc.sh install spoc-runner`. The user holds no SSH keys, `gh` token or browser profile. On the laptop, any job can still read world-readable files and reach the LAN. Accept that only because every job is reviewed.
- Devices, using the rules already on this laptop:
  - `99-platformio-udev.rules` gives `MODE="0666"` to ST-Link (`0483`) and the Zephyr USB VID (`2fe3`). These work for any user as-is; tightening them to `GROUP="plugdev", MODE="0660"` is better.
  - `99-rigol-usbtmc.rules` gives `/dev/usbtmc*` to `plugdev` 0660, so add `spoc-runner` to `plugdev`. Spins on CDC-ACM need `dialout`.
- Add stable names with udev `SYMLINK+=` keyed on serial numbers: `/dev/spoc-scope`, `/dev/spoc-cut`, `/dev/spoc-mux`. The scope enumerates as `/dev/usbtmc1` today, not `usbtmc0`. Pin the ST-Link by its serial in OpenOCD (`adapter serial`).
- Give the runner its own PlatformIO/Zephyr cache (`PLATFORMIO_CORE_DIR` under its home), pre-provisioned once, so jobs need no network for toolchains.

### One job at a time
- One runner process executes one job at a time. Register exactly one runner with the `spoc` label.
- The `concurrency: spoc-bench` group with `queue: max` serialises across workflows and keeps queued PRs instead of silently cancelling them, which is what the default `single` does. Don't set `cancel-in-progress`, because killing a campaign mid-reflash leaves the bench dirty.
- Keep the bench out of manual use while the service is up, or stop the service (`svc.sh stop`) during hand sessions. Otherwise a queued job may grab the ST-Link while you work.

### How the report reaches the reviewer
1. **Job summary**: `record.md` (verdict table) renders on the run page.
2. **Artifact**: the whole campaign folder, so `report.html` and its figures open after download. Optionally add `record.md` unzipped (`archive: false`). The default retention is 90 days on public repos.
3. **PR comment** from the GitHub-hosted `comment` job: PASS/FAIL counts plus links to the run and the artifact. Update one comment per PR rather than adding a new one per run.
4. **Pages** (later, optional): publish `control-center.html` and the latest reports from `main` only, as a public history. Never deploy Pages from the bench job.

### What transfers to a Raspberry Pi
Unchanged: the workflow YAML (labels `[self-hosted, linux, spoc]`), the environment, the hook script, the udev rules and symlinks, the `spoc-runner` user layout and the systemd service.

Changes:
- Download the `linux-arm64` runner tarball.
- Register a new runner (`config.sh` with a fresh token) and remove the laptop one.
- Re-provision PlatformIO/Zephyr toolchains for `linux_aarch64`. **Unverified:** that every package `pio_tests.ini` pins (toolchain, OpenOCD, Zephyr SDK) exists for aarch64. Check with a dry build before moving the bench.

The Pi is the better security posture. It is a dedicated box with nothing personal on it, and it can be re-imaged from a golden SD card. That makes ephemeral (`--ephemeral`/JIT) registration plus a periodic re-image a practical upgrade path.

## 3. Pitfalls
- A `pull_request:` workflow plus a self-hosted runner on a public repo lets any approved (or previously merged) contributor run arbitrary code on your laptop. The current `first_time_contributors` setting is too weak for that.
- Checking out `github.head_ref` instead of the SHA lets the fork push new code between review and run (TOCTOU). Always use `head.sha` or the dispatched `sha`.
- Interpolating `${{ github.event.pull_request.title }}`, branch names or inputs straight into `run:` is script injection. Pass them through `env:` instead. [Hardening](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- `actions/checkout` defaults to persisting the token in `.git/config`. Use `persist-credentials: false`.
- `actions/checkout` cleans the workspace, so `tests/out/` (git-ignored) is wiped on the next run. The control center's history therefore lives only in artifacts unless you point the campaign output to a persistent directory outside the workspace.
- `report.html` is not self-contained: relative PNGs break in a single-file artifact. Zip the folder, or inline the figures as data URIs if browser preview matters.
- The default concurrency queue (`single`) silently cancels a pending campaign when a third is queued. Use `queue: max`.
- A failed or cancelled job can leave the CUT on test firmware, the scope armed or a Spin in fault. Always run an `if: always()` safe-state step, and treat the post-job hook as a backstop.
- The runner auto-updates and must stay within 63 days of the latest release. A laptop that sleeps through a queued job leaves it pending (queued jobs time out after 24 h, **unverified** on current docs).
- A laptop asleep or off is a runner offline. The PR just waits, which is fine. Say so in the PR template.
