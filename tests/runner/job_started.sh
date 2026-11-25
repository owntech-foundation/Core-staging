#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file job_started.sh
## @brief Pre-job hook of the SPOC bench runner: refuses every job but spoc.yml from main.
##
## The runner runs it before each job (ACTIONS_RUNNER_HOOK_JOB_STARTED in the runner's .env). A non-zero exit
## fails the job before any of its steps run. install.sh copies it to /usr/local/lib/spoc-runner/, root-owned and
## not writable by the runner user, so no job can change it.
allowed="owntech-foundation/Core-staging/.github/workflows/spoc.yml@refs/heads/main"
if [ "${GITHUB_WORKFLOW_REF:-}" != "$allowed" ]; then
    echo "spoc-runner: refused workflow '${GITHUB_WORKFLOW_REF:-unset}' (event ${GITHUB_EVENT_NAME:-?}); only $allowed may use the bench" >&2
    exit 1
fi
case "${GITHUB_EVENT_NAME:-}" in
    pull_request_target|workflow_dispatch) ;;
    *) echo "spoc-runner: refused event '${GITHUB_EVENT_NAME:-unset}'" >&2; exit 1 ;;
esac
echo "spoc-runner: $GITHUB_WORKFLOW_REF on $GITHUB_EVENT_NAME by ${GITHUB_ACTOR:-?}"
