# Core upstream workflow

How contributions reach owntech-foundation/Core: through Core-staging, the SPOC bench and graduation. Terms used on the map (Core-staging#1) and its tickets.

## Language

**Core**:
The owntech-foundation/Core repository: the released firmware base everyone builds on.

**Staging**:
This repository (owntech-foundation/Core-staging): a temporary home for experimental work and the map, archived once the gate runs on Core directly.
_Avoid_: dev fork, integration fork

**Contribution pipeline**:
The agreed route from a PR to Core: build checks, bench campaign, graduation report, sign-off, graduation PR.
_Avoid_: workflow (alone), CI

**Small fix**:
A PR that corrects existing behaviour without new API; merged into Core directly once a bench test exercising it passes.

**Experimental work**:
New features or reworks; land in Staging first and reach Core only by graduation.

**SPOC bench**:
The hardware test setup: a CUT Spin, a MUX Spin, an ST-Link and the MHO984 scope, driven by a host computer.
_Avoid_: test bench (alone), rig

**CUT**:
The Spin under test, running the test firmware.

**MUX**:
The Spin routing CUT pins to the scope channels and injecting test signals.

**Campaign**:
One run of the test runner on one commit, producing a record and a review page.
_Avoid_: test run, job

**Gate**:
The set of tests a change must pass, chosen by what the change touches.

**Graduation**:
Moving a feature from Staging into Core through one curated PR, after which the Staging PRs behind it are closed.
_Avoid_: upstreaming (for this step), promotion

**Graduation report**:
The campaign report a human reads to decide on graduation.

**Sign-off**:
A human's validation of a graduation report; may be the author's own, stated in the report.

**Bare CUT**:
The CUT with no power board under it, on the SPOC bench as wired.

**Regression suite**:
Every test that covers Core's API, each placed in a tier; a gate selects from it.
_Avoid_: test suite (alone)

**Tier**:
When and where a test runs: build-only, per-PR, nightly, or supervised.

**Supervised campaign**:
A campaign run with a person present, for tests needing a power stage or hand-held wires or probes; its record names who supervised it.

**Coverage matrix**:
Each API call, the test that covers it and its tier, or the reason it is a gap.

**Known failure**:
A test that fails on the baseline as well as on the change: a bug that was already there, tracked by its own ticket.

**Reference run**:
A repeated timing measurement whose mean and spread later timing results are compared against.
