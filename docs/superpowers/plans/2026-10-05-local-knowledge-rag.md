# Local Knowledge RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add deterministic, source-bearing local knowledge retrieval to initial and feedback model planning without changing traffic effect evidence or eligibility.

**Architecture:** A pure retriever validates and ranks the curated JSON catalog. Model inputs carry at most three snippets. A citation checker and retrieval auditor keep emitted IDs and saved inputs attributable; existing native evidence remains authoritative.

**Tech Stack:** Python 3.12 standard library, existing TranStar model and audit code, unittest.

**Spec:** `docs/superpowers/specs/2026-10-05-local-knowledge-rag-design.md`

## Global Constraints

- Never copy API credentials into the catalog, payload, report or tests.
- Do not retrieve historical experiment results as evidence of current treatment effect.
- Do not change the frozen recommendation thresholds or historical wire inputs.
- Preserve the existing 827-test offline suite and native record checks.

---

### Task 1: Deterministic retrieval

**Files:** Create `traffic_agent/knowledge_retrieval.py`; create `tests/test_knowledge_retrieval.py`.

**Interfaces:** `retrieve(request: str, phase: str, scenario: str | None = None) -> dict`; `verify(bundle: dict, request: str, phase: str, scenario: str | None = None) -> str` returns `verified` or `catalog_changed`.

- [x] Write tests for bounded, query-sensitive results, malformed catalog, no-match and deterministic replay.
- [x] Run `python -m unittest tests.test_knowledge_retrieval -q` and confirm the new tests fail before implementation.
- [x] Implement validation, deterministic ranking and content hashing with Python standard library only.
- [x] Run the focused tests to pass, then inspect source labels for unsupported effect claims.

### Task 2: Planning integration and citation check

**Files:** Modify `traffic_agent/natural_plan.py`, `traffic_agent/feedback_planner.py`, `traffic_agent/planning_instructions.py`, `traffic_agent/planning_audit.py`; extend focused tests.

**Interfaces:** `validate_citations(value: object, bundle: dict) -> None` rejects unknown `【K:id】` references; existing model payloads gain `knowledge_retrieval` only for new plans.

- [x] Add focused tests proving initial and feedback wire inputs include the retrieved source excerpts and that fabricated citation IDs fail.
- [x] Run focused tests and confirm the new expectations fail.
- [x] Add retrieval to the initial payload/plan, then to feedback context for new plans; instruct the model about knowledge versus measured refs.
- [x] Validate citations and audit the saved feedback bundle without rewriting historical decisions.
- [x] Run all touched Python tests and inspect request byte hashes.

### Task 3: Delivery and regression

**Files:** Modify `traffic_agent/portable.py`, `tests/test_portable.py`, `docs/20261005本地知识检索增强.md`.

**Interfaces:** The portable manifest requires the retriever and catalog; docs explain the input and evidence boundaries.

- [x] Add a portable-file omission test and update the sealed-file set.
- [x] Run `scripts/run_offline_tests.py` and two archived programme verifiers with `--no-write`.
- [x] Check `git diff --check` and prepare source/tests/docs for delivery.
- Delivery: commit and push the current branch after the completed verification.
