# V2 Migration Boundary and Stage 0B Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document one canonical v2 decision chain and add a PIT NewsGrab archive that reuses the repository's existing collector endpoint configuration.

**Architecture:** The roadmap defines v1 as reusable infrastructure and a migration baseline, not a second post-v2 decision engine. The archive stays an offline evidence producer: it reads `GOOGLE_NEWS_COLLECTOR_URL`, writes immutable DSA-owned evidence, and never changes the live Agent news request or user-visible report.

**Tech Stack:** Python 3.13, pytest, standard-library HTTP client, Markdown.

## Global Constraints

- The final user-facing product has one v2 decision output; migration shadow runs and v1 history are version-isolated.
- No auto-ordering, live LLM call, default DSA rule change, API/Web change, or second NewsGrab Docker stack.
- `GOOGLE_NEWS_COLLECTOR_URL` is the single existing collector endpoint setting; a CLI `--base-url` remains an explicit override.
- Archive records remain write-once and preserve failure evidence.

---

### Task 1: Document the migration boundary

**Files:**
- Create: `research/mainline/DSA_v2目标架构与递进验证路线.md`

**Interfaces:**
- Produces: an authoritative explanation of the final v2-only output, the v1-to-v2 transition, and the role of offline PIT validation.

- [ ] **Step 1: Write the architecture and migration sections**

State that v1 data providers, UI, notification, and storage infrastructure can be reused, but v1 analysis conclusions do not run after v2. State that shadow results are isolated and that v2 becomes the sole user-visible decision output after promotion.

- [ ] **Step 2: Review for scope and terminology**

Run: `rg -n "二次|唯一|影子|PIT|自动下单" research/mainline/DSA_v2目标架构与递进验证路线.md`

Expected: the document prohibits a second decision engine and automatic orders, while preserving PIT as offline validation.

### Task 2: Align the archive CLI with the existing endpoint setting

**Files:**
- Create: `research/prototype/newsgrab_archive/runner.py`
- Test: `tests/test_newsgrab_archive_runner.py`

**Interfaces:**
- Consumes: `GOOGLE_NEWS_COLLECTOR_URL` and optional CLI `--base-url`.
- Produces: `build_parser()` whose base URL defaults to the existing setting and requires `--base-url` only when that setting is absent.

- [ ] **Step 1: Write the failing test**

```python
def test_cli_uses_existing_google_news_collector_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_NEWS_COLLECTOR_URL", "http://collector.example")
    args = build_parser().parse_args(["--query", "600519", "--scope", "company"])
    assert args.base_url == "http://collector.example"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_newsgrab_archive_runner.py::test_cli_uses_existing_google_news_collector_env -v`

Expected: FAIL because the archive module is not yet present.

- [ ] **Step 3: Add the archive package and minimally implement the configuration contract**

Implement the existing forward archive contracts and use `os.getenv("GOOGLE_NEWS_COLLECTOR_URL")` for the parser default. Preserve explicit `--base-url` override and do not add a second endpoint environment variable.

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/test_newsgrab_archive_*.py -v`

Expected: all archive tests pass.

### Task 3: Add the archive evidence contracts and documentation

**Files:**
- Create: `research/prototype/newsgrab_archive/__init__.py`, `archive.py`, `availability.py`, `client.py`, `models.py`
- Create: `tests/test_newsgrab_archive_availability.py`, `tests/test_newsgrab_archive_client.py`, `tests/test_newsgrab_archive_writer.py`
- Create: `docs/newsgrab-archive.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: NewsGrab HTTP job API and one `ArchiveRequest`.
- Produces: write-once manifest, article/error JSONL files, SHA-256 evidence, and strict PIT availability classification.

- [ ] **Step 1: Copy only the evidence-layer code and tests**

Exclude `.env.example`, Docker Compose files, and the live Agent client because the target repository already owns those paths.

- [ ] **Step 2: Document coexistence with the live integration**

Document that live Agent search and archive capture are separate consumers of the same configured collector. The archive neither modifies a live analysis nor backfills pre-archive historical evidence.

- [ ] **Step 3: Verify syntax, tests, and diff hygiene**

Run: `python -m py_compile research/prototype/newsgrab_archive/*.py`

Run: `python -m pytest tests/test_newsgrab_archive_*.py -v`

Run: `git diff --check`

Expected: all commands pass.
