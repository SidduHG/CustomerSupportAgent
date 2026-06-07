# CLAUDE.md

Guidance for Claude Code when working in this repository.

---

## Project

**Voice Customer Support Agent** — a voice-driven support bot that listens to a
caller's question, retrieves grounded answers from a knowledge base using
**hybrid search** (semantic + keyword + reranking), and speaks the answer back.

The project is built in three phases (see the phase docs for full scope):

| Phase | Doc | Scope |
|-------|-----|-------|
| 1 — Foundation | `voice-support-agent/PHASE_1_FOUNDATION.md` | Voice loop + KB MCP server + single Resolver agent |
| 2 — Triage | `voice-support-agent/PHASE_2_TRIAGE.md` | Classifier, CRM/Email MCP, confidence gate, multi-agent handoff |
| 3 — Production | `voice-support-agent/PHASE_3_PRODUCTION.md` | Hardening, observability, deployment |

All application code lives under `voice-support-agent/`.

### Stack

- **Python 3.11+**
- **Pipecat** — voice pipeline orchestration (VAD → STT → LLM → TTS)
- **Groq API** — LLM inference (Llama 3.3 70B) + Whisper STT
- **Kokoro TTS** — self-hosted text-to-speech (Docker)
- **FastMCP** — KB / CRM / Email MCP servers
- **ChromaDB** + **sentence-transformers** + **rank-bm25** — hybrid retrieval
- **loguru** — structured logging; **python-dotenv** — config

### Layout (under `voice-support-agent/`)

```
agent/            # pipeline, core capabilities, API, db, config, observability
mcp_servers/      # kb_mcp (+ crm_mcp, email_mcp in later phases)
scripts/          # ingest_docs.py, seed_data.py
frontend/         # React UI (chat + dashboard)
infra/            # docker-compose, nginx
tests/            # unit/ and integration/
evals/            # eval harness + test cases
```

---

## Conventions

- **Secrets live in `.env` only** — never hardcode keys or commit `.env`. Keep
  `.env.example` updated whenever a new variable is introduced.
- **Config via environment variables** — read through `agent/config/settings.py`,
  not scattered `os.getenv` calls in business logic.
- Match the surrounding code's style, naming, and structure. Keep modules thin
  and single-purpose (the existing `chunker` / `embedder` / `hybrid_search` split
  is the model to follow).
- Logging goes through `loguru`, not `print`.

---

## Git & PR Workflow  *(required — follow on every change)*

1. **Branch per feature.** Never commit directly to `main`. Start each new
   feature or fix on its own branch:
   ```
   git checkout -b feat/<short-name>     # or fix/, chore/, docs/
   ```

2. **Small, logical commits.** Do **not** dump all work into a single commit.
   Commit each coherent unit of work separately (e.g. "add chunker", "add BM25
   index", "wire MCP tool") with a clear message describing the *why*.

3. **Test before pushing.** Before pushing a branch, run **both** unit and
   integration tests — they must pass:
   ```
   pytest tests/unit -v
   pytest tests/integration -v
   ```
   Do not push if tests fail; fix the cause first.

4. **PR to `main` with a proper description.** Open the PR against `main`. The
   description must cover: what changed, why, how it was tested, and any
   follow-ups. Use the `gh` CLI.

5. **Review the PR with an agent.** After the PR is raised, spawn a review agent
   to review the PR thoroughly. The agent should:
   - Identify correctness bugs, gaps, and cleanups.
   - Post its findings as comments on the PR.
   - Apply the corrections on the branch.
   Then re-run the tests (step 3) and **update the PR** with the fixes. Repeat
   until the review is clean.

> Commit messages end with the `Co-Authored-By` trailer. PR bodies end with the
> Claude Code generation note. Only commit or push when asked.
