# GitHub Actions Delivery Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitHub the active hosted CI platform while retaining the existing GitLab compatibility configuration.

**Architecture:** One GitHub Actions workflow calls the repository's existing Makefile targets for tests, quality checks, binary construction, and smoke tests. A contract test parses the workflow so hosted CI cannot silently drift from those commands; delivery documents identify GitHub as the active platform without inventing a pass result before push.

**Tech Stack:** GitHub Actions, YAML, Python 3.12, PyInstaller, pytest, PyYAML.

## Global Constraints

- Keep `.gitlab-ci.yml` and its exact `unit-test` job.
- GitHub jobs run on `ubuntu-latest` with Python `3.12`.
- Do not add secrets, public deployment, Docker, package-manager publication, or GitHub Release automation.
- Upload only `dist/guarded-agent` as artifact `guarded-agent-linux-x86_64`.

---

### Task 1: Add and document GitHub hosted CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `tests/test_packaging.py`
- Modify: `README.md`, `SPEC.md`, `PLAN.md`, `SPEC_PROCESS.md`, `AGENT_LOG.md`

**Interfaces:**
- Consumes: `make test`, `make quality`, `make binary`, `dist/guarded-agent`
- Produces: GitHub checks `unit-test` and `build-binary`, artifact `guarded-agent-linux-x86_64`

- [ ] **Step 1: Write the failing workflow contract test**

Add a test that loads `.github/workflows/ci.yml`, confirms both jobs use Python 3.12, checks the Makefile/smoke commands, and verifies the uploaded artifact name and path.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/pytest tests/test_packaging.py -q`

Expected: FAIL because `.github/workflows/ci.yml` does not exist.

- [ ] **Step 3: Implement the minimal workflow and documentation updates**

Create the workflow with `pull_request` and `push` triggers, read-only contents permission, dependency installation, repository commands, and `actions/upload-artifact`. Update delivery documents to name GitHub as the current hosted platform and GitLab as compatibility configuration.

- [ ] **Step 4: Verify locally**

Run: `.venv/bin/pytest tests/test_packaging.py -q && make test && make quality && make binary && ./dist/guarded-agent version && ./dist/guarded-agent demo && git diff --check`

Expected: all commands return zero; the demo prints all three safety scenarios.

- [ ] **Step 5: Commit and push**

```bash
git add .github/workflows/ci.yml tests/test_packaging.py README.md SPEC.md PLAN.md SPEC_PROCESS.md AGENT_LOG.md docs/superpowers/plans/2026-08-10-github-actions.md
git commit -m "ci: run guarded agent checks on github"
git push
```
