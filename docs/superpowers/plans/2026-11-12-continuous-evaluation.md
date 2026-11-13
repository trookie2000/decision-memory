# Continuous Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start one independent background process that generates and evaluates six new cases, then writes a combined report.

**Architecture:** A durable Series wraps the existing Battle state machine. Each case retains independent sources, references, predictions and judgments. The process inherits credentials once, checks connectivity before remote writes and uses fresh dispatcher contexts per operation.

**Tech Stack:** Existing Python 3.11+ standard library, argparse, subprocess, file locks, Mario MCP and Docs CLI.

**Spec:** docs/superpowers/specs/2026-11-12-continuous-evaluation-design.md

## Global Constraints

- Fixed group 7419479552888854 and the existing two employees only; no foreground UI or remote-control changes.
- Six new cases; preserve S003 and the prepared official-stage artifacts.
- Credentials remain in process environments, never argv, logs or project files.
- Unknown sends are not automatically resent; all results remain Agent initial assessments.
- No commit or push in this task; preserve the current task branch and MyFlicker changes.

## Review Focus

- Restart after a completed case must not repeat its employee messages or model calls.
- A missing credential must stop before any Docs creation or group message.
- A malformed dispatch receipt must produce a useful blocked status instead of silently polling forever.
- Concurrent start/resume must not create two active controllers for one output directory.
- Stop requests must preserve in-flight results for recovery without sending another task.

### Task 1: Isolated dispatch contexts with legacy compatibility

**Files:** decision_memory/collaboration.py, decision_memory/coordinator.py, tests/test_coordinator.py.

**Interfaces:** BattleConfig.dispatch_context accepts task (default) or operation. In operation mode each operation stores its own dispatcher thread and audit verifies that binding. Battle API stays compatible.

- [ ] Write tests for independent contexts through a real CLI/HTTP fixture cycle and rejection of a mismatched per-operation receipt; run and observe RED.
- [ ] Implement the configuration and per-operation binding. Clear obsolete extraction errors once a valid prediction is accepted.
- [ ] Run coordinator/collaboration tests; confirm legacy shared-thread behavior still passes.

### Task 2: Persistent batch controller, CLI and real launch

**Files:** create decision_memory/series.py, tests/test_series.py, examples/continuous-cases.json; modify decision_memory/cli.py, README.md and docs/coordinator.md.

**Interfaces:** Series.create(root, battle_config, model_config, cases, mode='baseline', max_seconds=7200); Series.run(retry_extraction=False), Series.summary(), Series.launch(), Series.request_stop(). CLI: series start/resume/status/stop/run.

- [ ] Write integration tests for two cases completing automatically, no work repeated on resume, missing-key preflight, stop/resume, manifest tampering and unknown delivery; observe RED.
- [ ] Implement atomic batch state, configuration/manifest integrity checks, exclusive run lock, startup preflight, case transitions and summary report. Child processes inherit the supplied environment.
- [ ] Add detached CLI launch, PID/log/status output, cooperative stop and six distinct fictional briefs. Keep the existing scoring protocol unchanged.
- [ ] Run meaningful integration checks and the full existing suite, then request one fresh review of the changed code; fix material findings with regression tests.
- [ ] Launch the six-case batch in the authorized host environment with the already provided key handed over in memory. Verify real employee receipts and that finishing one case advances to the next.
- [ ] Record results and handoff commands. Keep the process running until six cases finish or a stated external blocker occurs; never call a stopped process running.
