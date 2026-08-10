# Guarded Agent WebUI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unusable plain HTML control surface with a polished Chinese Agent workbench that supports automatic light/dark themes, responsive layouts, accessible controls, and clear task/approval workflows.

**Architecture:** Keep FastAPI and Jinja2 server rendering as the source of truth. A shared template shell and macro library render the design system; one offline CSS file owns layout and components, while one small JavaScript file owns theme preference, disclosure helpers, copy feedback, and status polling. Backend changes are limited to safe presentation context and never expand the WebUI's authority.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, semantic HTML, native CSS, native JavaScript, pytest, HTTPX ASGITransport, PyInstaller.

## Global Constraints

- Bind only to `127.0.0.1` and one startup-fixed trusted workspace.
- Do not add npm, CDN assets, remote fonts, frontend frameworks, arbitrary command input, Web credential input, accounts, or public deployment.
- Preserve CSRF validation, configured-validator selection, one-active-task limit, approval binding, read-only settings, and CLI-only credential management.
- All user-facing interface copy is Chinese; raw enum/event values may remain visible for diagnostics.
- Theme modes are `system`, `light`, and `dark`; persist only the theme string in `localStorage`.
- Support 360px viewport width without page-level horizontal overflow; respect reduced motion and visible keyboard focus.

---

### Task 1: Shared workbench shell and visual system

**Files:**
- Create: `src/guarded_agent/templates/_macros.html`
- Modify: `src/guarded_agent/templates/base.html`
- Modify: `src/guarded_agent/static/app.css`
- Modify: `src/guarded_agent/web.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: existing `page(request, name, **context)` helper and Jinja2 environment
- Produces: `active_page`, `page_title`, `workspace_name` template context; `status_badge(status)` and `icon(name)` macros; `.app-shell`, `.sidebar`, `.workspace-main`, `.context-panel`, `.status-badge` CSS contracts

- [ ] **Step 1: Write failing shell semantics test**

Add an ASGI test that requests `/` and asserts Chinese product/navigation copy, `<html data-theme="system">`, a theme button with accessible label, `aria-current="page"` on tasks, localhost/workspace indicators, semantic `<nav>` and the three workbench layout classes.

- [ ] **Step 2: Run focused test and verify RED**

Run: `python -m pytest tests/test_web.py -q`

Expected: FAIL because the existing English single-column template lacks the required shell and theme controls.

- [ ] **Step 3: Implement shared shell, macros, and tokens**

Pass `active_page`, `page_title`, and `workspace_name` from `page()`. Replace `base.html` with the semantic app shell and inline pre-paint theme initialization. Add macro-based inline SVG icons and status badge markup. Replace the one-line stylesheet with offline tokens, reset, sidebar/header, panel, card, badge, form, button, focus, dark-theme, reduced-motion, and responsive rules.

- [ ] **Step 4: Verify Task 1 GREEN**

Run: `python -m pytest tests/test_web.py -q && python -m ruff check src tests && python -m mypy src`

Expected: shell test and all existing Web security tests pass; static checks return zero.

- [ ] **Step 5: Commit**

```bash
git add src/guarded_agent/templates/_macros.html src/guarded_agent/templates/base.html src/guarded_agent/static/app.css src/guarded_agent/web.py tests/test_web.py
git commit -m "feat: add responsive agent workbench shell"
```

---

### Task 2: Task, approval, memory, and settings experiences

**Files:**
- Modify: `src/guarded_agent/templates/tasks.html`
- Modify: `src/guarded_agent/templates/task_detail.html`
- Modify: `src/guarded_agent/templates/approvals.html`
- Modify: `src/guarded_agent/templates/memories.html`
- Modify: `src/guarded_agent/templates/settings.html`
- Modify: `src/guarded_agent/web.py`
- Modify: `src/guarded_agent/static/app.css`
- Modify: `tests/test_web.py`

**Interfaces:**
- Consumes: Task, Approval, MemoryEntry, CredentialStatus and Task 1 macros/layout
- Produces: task cards, event timeline, risk approval cards, memory cards/forms, read-only safety settings; `task_count`, `active_task`, localized display labels, short IDs, and formatted timestamps

- [ ] **Step 1: Write failing page-information tests**

Add focused assertions for: task cards and right-side create form; task detail header/timeline/disclosures/cancel panel; approval risk copy and distinct reject/approve labels; memory entry/create panels; settings safety and CLI-only credential copy. Assert existing form action, field name, CSRF token, task/approval ID and validator selector contracts remain unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_web.py -q`

Expected: FAIL on missing Chinese page structure and component classes while existing route behaviors continue passing.

- [ ] **Step 3: Implement page templates and presentation context**

Render each page through the shared shell. Add safe derived display data in `web.py` only where Jinja cannot express it clearly; never parse new user input or expose credential contents. Use `<details>` for payloads and preserve raw diagnostic values alongside localized labels.

- [ ] **Step 4: Verify Task 2 GREEN**

Run: `python -m pytest tests/test_web.py -q && make test && make quality`

Expected: all tests and quality checks return zero on supported Python; Python 3.14 may retain its documented ASGI skips.

- [ ] **Step 5: Commit**

```bash
git add src/guarded_agent/templates src/guarded_agent/static/app.css src/guarded_agent/web.py tests/test_web.py
git commit -m "feat: redesign guarded agent control pages"
```

---

### Task 3: Theme behavior, live updates, visual verification, and evidence

**Files:**
- Modify: `src/guarded_agent/static/app.js`
- Modify: `src/guarded_agent/templates/base.html`
- Modify: `src/guarded_agent/templates/task_detail.html`
- Modify: `src/guarded_agent/static/app.css`
- Modify: `tests/test_web.py`
- Modify: `README.md`
- Modify: `AGENT_LOG.md`
- Create: `docs/screenshots/webui-light.png`
- Create: `docs/screenshots/webui-dark.png`

**Interfaces:**
- Consumes: `[data-theme-toggle]`, `[data-status-url]`, `[data-status-value]`, `[data-timeline]` DOM hooks
- Produces: three-state theme control with key `guarded-agent-theme`; status/timeline refresh that preserves server authority; two review screenshots from the real localhost app

- [ ] **Step 1: Write failing static behavior contract tests**

Add tests that inspect served JavaScript/CSS/template content for the exact theme modes and storage key, system-theme media listener, status/timeline hooks, error-tolerant polling, responsive breakpoint, reduced-motion rule, focus-visible rule and mobile navigation label.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_web.py -q`

Expected: FAIL because the current script only updates one `<strong>` and has no theme state machine.

- [ ] **Step 3: Implement theme and live-update behavior**

Implement a deterministic theme cycle `system → light → dark → system`, update accessible labels/icons, react to system theme changes only in system mode, and safely ignore storage failures. Extend polling to update status and timeline DOM from the existing same-origin JSON endpoint without accepting HTML from the API.

- [ ] **Step 4: Run full automated verification**

Run: `make test && make quality && make binary && ./dist/guarded-agent version && ./dist/guarded-agent demo && git diff --check`

Expected: all commands return zero and the demo prints all three deterministic safety scenarios.

- [ ] **Step 5: Capture and inspect real screenshots**

Install Playwright only in the local audit environment with `python -m pip install playwright` and `python -m playwright install chromium`; do not add it to `pyproject.toml` or the distributed binary. Start `guarded-agent web` against a temporary configured workspace on `127.0.0.1`. Capture 1440px light and dark task-workbench screenshots, plus a 390px overflow check. Inspect the images for hierarchy, clipping, focus, contrast, theme consistency, and clear primary/danger actions; fix visual defects and repeat the full automated verification if code changes.

- [ ] **Step 6: Update documentation and commit**

Document the new local workbench, theme behavior and screenshot evidence in README and AGENT_LOG without claiming public deployment or Open Design usage.

```bash
git add src/guarded_agent/static/app.js src/guarded_agent/templates src/guarded_agent/static/app.css tests/test_web.py README.md AGENT_LOG.md docs/screenshots
git commit -m "feat: polish and verify local agent workbench"
```

- [ ] **Step 7: Request two-stage review and prepare PR**

Review first against the approved design, then for HTML/CSS/JS quality, accessibility, security regressions, responsive behavior and screenshot evidence. Fix every Critical and Important finding, rerun the full gate, push `feature/webui-redesign`, and open a PR to `main` with task attribution and human design decisions.
