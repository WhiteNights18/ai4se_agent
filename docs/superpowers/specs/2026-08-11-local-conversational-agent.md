# Local Conversational Agent Design

## Goal

Add a local-only WebUI conversation surface that lets one user send successive messages to a governed AgentLoop without re-entering the vault password for every message.

## Scope

- The CLI `web --provider openai-compatible` unlocks the encrypted vault once at server startup.
- The process keeps only the decrypted provider credential in memory; the vault password is never stored.
- A single workspace has one active conversation/task at a time, matching the existing governance constraint.
- Each user message is persisted as a bounded conversation message and becomes the next AgentLoop user turn.
- Existing tool governance, workspace boundary, approval flow, audit events, validation commands, redaction, and CSRF protection remain authoritative.
- The UI uses same-origin JSON POST/GET endpoints and text-only rendering; no public bind or streaming transport is introduced.

## Non-goals

- Multi-user authentication or remote deployment.
- Conversation sharing, file uploads, arbitrary shell input, or model-generated HTML.
- Persisting the vault password or decrypted API key.
- Replacing the existing deterministic AgentLoop or governance rules.

## Data flow

```text
WebUI startup → unlock vault once → create provider-backed service
User message → CSRF-checked POST → persist message → one governed AgentLoop step
             → persist action/feedback/audit → JSON response
Browser      → render text-only transcript, status, approval affordances
```

The conversation record stores role (`user` or `agent`), bounded text, task id, and timestamp. Agent actions and tool results remain in the existing task-turn and audit tables so no secret-bearing provider response is copied into the transcript.

## Safety and lifecycle

- The provider is constructed only when the CLI explicitly selects `openai-compatible`.
- WebUI remains bound exclusively to `127.0.0.1`.
- Every mutating endpoint requires the existing strict CSRF cookie/form token.
- User messages are length-limited and stored as plain text; responses are rendered with `textContent`.
- The server returns `WAITING_APPROVAL` without executing the pending mutating action; existing approval pages resume it.
- Restarting the WebUI requires vault unlock again.

## Acceptance criteria

1. Starting the real-provider WebUI prompts for the vault password once, not once per message.
2. A conversation message can advance the governed task and return status, latest feedback, and transcript JSON.
3. Existing mock WebUI behavior and CLI `run` behavior remain unchanged.
4. Tests cover message persistence, same-workspace access, CSRF rejection, provider-backed message flow, and secret non-disclosure.
5. `make test`, `make quality`, `make binary`, `version`, `demo`, and `git diff --check` pass.

