# Decision Memory Implementation Plan

> For agentic workers, use superpowers:executing-plans to implement task by task. User authorized implementation in the current conversation.

**Goal** Build a reproducible, evidence-grounded CLI for competition task 07.

**Architecture** Stable source indexing feeds baseline and verified LLM extraction. Exact evidence validation and bounded repairs protect the output contract. Separate batch and review commands provide reproducible experiments without injecting annotations into inference.

**Tech Stack** Python 3.11+, standard library, unittest, configurable Chat Completions HTTP gateway.

**Spec** ../specs/2026-11-09-decision-memory-design.md

## Global Constraints

- One stage per input, annotation files excluded from inference.
- Credentials in environment only, redact HTTP error bodies.
- Missing reasons and boundaries are “未说明”.
- Stable exact citations, bounded requests and repairs, atomic output writes.
- Actual model performance and local transport tests are reported separately.

## Review Focus

- Unicode/BOM/CRLF and blank lines retain verifiable source locations.
- Fabricated evidence and malformed fields fail closed.
- Partial HTTP/batch failure never produces a successful empty result.
- Resume detects changed inputs, settings, prompt versions and tampered output.
- Unreviewed and duplicate judgments cannot inflate local evaluation metrics.

## Task 1: Source indexing and output validation

Files: decision_memory/sources.py, decision_memory/validation.py, tests/test_sources.py, tests/test_validation.py.

Interfaces: load_sample(dialogue, artifact, stage) returns Sample with evidence catalog and digest. validate_result(payload, sample) returns a normalized result or raises ValueError.

- [x] Write source location, official ZIP discovery and fabricated-quote regression tests.
- [x] Run python3 -m unittest discover -s tests -v and observe missing feature failures.
- [x] Implement source indexing, stage-scoped dataset reading and strict output validation.
- [x] Run the same suite. Expected all source and validation tests pass.

## Task 2: Model calls and extraction pipeline

Files: decision_memory/client.py, decision_memory/prompts.py, decision_memory/pipeline.py, tests/test_pipeline.py.

Interfaces: ModelClient.complete(system, user) returns parsed JSON plus usage, retries and elapsed time. extract(sample, client, mode) returns validated JSON with provenance, trace and usage.

- [x] Write local HTTP integration tests for successful extraction, 429 retry, auth failure, invalid JSON and unsupported citations.
- [x] Observe failures, implement configurable gateway and bounded extraction/repair/review.
- [x] Run python3 -m unittest discover -s tests -v. Expected full suite passes.

## Task 3: CLI, batch execution and human evaluation

Files: decision_memory/cli.py, decision_memory/evaluation.py, decision_memory/__main__.py, pyproject.toml, config.example.toml, tests/test_cli.py, tests/test_evaluation.py.

Interfaces: inspect/extract/batch/validate/review commands produce deterministic files and nonzero failure exit status. Review computes only explicitly assessed coverage, correctness and citations, and shows remaining review work.

- [x] Write subprocess tests for actual CLI invocation, interrupted batches, resume input changes and unreviewed judgments.
- [x] Observe failures, implement CLI, atomic writes, replay validation and evaluation workflow.
- [x] Run the full suite and all four official sample input inspections. Expected correct stage isolation and CLI exit statuses.

## Task 4: Real sample experiments and handoff

Files: README.md, docs/experiment-report.md, docs/ai-assistance.md.

- [x] User explicitly selected offline-first delivery because credentials are unavailable. Real-model experiments remain pending configuration.
- [x] Implemented and tested human-review worksheet generation. Real-model semantic errors, costs and latency remain pending.
- [x] Document installation, configuration, inputs/outputs, exception behavior and exact reproducibility commands.
- [x] Run final full suite, installable entry-point smoke check and independent code review.
- [x] Run Weaver recover preview before committing. Fix only attributable missed events. Record actual reporting result.

Implementation uses one final local commit after verification and Weaver checks. No remote publication is part of this task.
