# Phase 2 — Triage Intelligence: Multi-Agent + Confidence Gate

> **Goal:** Turn the single Phase 1 bot into a real triage system — a Classifier tags intent with a confidence score, a Resolver tries to fix it using CRM and Email tools, and an Escalator hands off to a human when confidence is too low.

---

## Overview

This is the core of the project. By the end you have:
- **Three agents** running as Pipecat subagents, each with one job
- **Pipecat Flows** managing the dialog state machine (greet → understand → act → close)
- **CRM MCP** backed by a real Postgres database
- **Email MCP** that sends confirmation emails
- **Confidence gate** that routes every call: auto-resolve, clarify, or escalate
- **Stateful handoff** — the human agent sees the full transcript and context

Phase 1 is a prerequisite. This phase extends it; nothing is rewritten.

---

## New Production Stack

| Tool | Version | Role | Cost |
|------|---------|------|------|
| **Pipecat Flows** | v1.0+ | Dialog state machine | Free (bundled with Pipecat) |
| **PostgreSQL** | 16 | CRM data store (customers, tickets) | Free |
| **psycopg2-binary** | 2.9+ | Postgres Python driver | Free |
| **SQLAlchemy** | 2.x | ORM for CRM models | Free |
| **aiosmtplib** | 3.x | Async SMTP for Email MCP | Free |
| **Redis** | 7 | Session context store (handoff state) | Free (BSD) |
| **redis-py** | 5.x | Redis Python client | Free |
| **Pydantic** | 2.x | Data validation for agent outputs | Free |
| **pytest-asyncio** | — | Async test support | Free |

All Phase 1 dependencies still apply.

---

## API Keys Required

| Key | Where to get | Cost |
|-----|-------------|------|
| `GROQ_API_KEY` | https://console.groq.com (from Phase 1) | Free tier |
| `SMTP_HOST` / `SMTP_PORT` | Your mail provider (Gmail, Mailgun, etc.) | Free tiers available |

Still no paid keys required for core functionality.

---

## Updated Project Structure

```
voice-support-triage/
├── .env                          # add new vars from below
├── docker-compose.yml            # add Postgres + Redis services
│
├── mcp_servers/
│   ├── kb_mcp/                   # unchanged from Phase 1
│   ├── crm_mcp/
│   │   ├── __init__.py
│   │   ├── server.py             # FastMCP CRM server
│   │   ├── models.py             # SQLAlchemy models
│   │   ├── db.py                 # DB connection pool
│   │   └── schema.sql            # initial schema + seed data
│   └── email_mcp/
│       ├── __init__.py
│       └── server.py             # FastMCP email server
│
├── agent/
│   ├── config.py                 # add confidence thresholds
│   ├── pipeline.py               # updated: multi-agent Pipecat flow
│   ├── classifier.py             # NEW: Classifier agent
│   ├── resolver.py               # updated: more tools (CRM + Email)
│   ├── escalator.py              # NEW: Escalator agent
│   ├── confidence_gate.py        # NEW: gate logic
│   ├── context.py                # NEW: shared call context dataclass
│   ├── tools.py                  # updated: CRM + Email + KB tool calls
│   └── flows/
│       └── support_flow.py       # NEW: Pipecat Flows state machine
│
├── db/
│   └── seed.sql                  # sample customers + tickets
│
└── tests/
    ├── test_classifier.py
    ├── test_confidence_gate.py
    └── test_crm_mcp.py
```

---

## Environment Variables (additions to Phase 1)

```bash
# .env — Phase 2 additions

# ── CRM Database (Postgres) ────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=support_crm
POSTGRES_USER=support_user
POSTGRES_PASSWORD=changeme_in_prod
DATABASE_URL=postgresql://support_user:changeme_in_prod@localhost:5432/support_crm
CRM_MCP_PORT=8001

# ── Email MCP ──────────────────────────────────────────────
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_FROM=support@yourcompany.com
EMAIL_MCP_PORT=8002

# ── Redis (session / handoff context) ──────────────────────
REDIS_URL=redis://localhost:6379

# ── Confidence Gate Thresholds ─────────────────────────────
CONFIDENCE_AUTO_RESOLVE=0.85
CONFIDENCE_CLARIFY=0.60

# ── Classifier model (fast, cheap) ─────────────────────────
GROQ_CLASSIFIER_MODEL=llama-3.1-8b-instant
```

---

## Step-by-Step Implementation

### Step 1 — Add Postgres + Redis to Docker Compose

```yaml
# docker-compose.yml — updated
version: "3.9"
services:
  kokoro-tts:                     # from Phase 1, unchanged
    image: ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2
    ports: ["8880:8880"]
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: support_crm_db
    environment:
      POSTGRES_DB:       support_crm
      POSTGRES_USER:     support_user
      POSTGRES_PASSWORD: changeme_in_prod
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./mcp_servers/crm_mcp/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: support_redis
    ports: ["6379:6379"]
    restart: unless-stopped

volumes:
  pgdata:
```

```bash
docker-compose up -d postgres redis
```

---

### Step 2 — CRM Database Schema

```sql
-- mcp_servers/crm_mcp/schema.sql
CREATE TABLE IF NOT EXISTS customers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(120) NOT NULL,
    email       VARCHAR(200) UNIQUE NOT NULL,
    phone       VARCHAR(30),
    plan        VARCHAR(50) DEFAULT 'free',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tickets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id  UUID REFERENCES customers(id),
    subject      VARCHAR(200) NOT NULL,
    status       VARCHAR(30) DEFAULT 'open',   -- open | resolved | escalated
    priority     VARCHAR(20) DEFAULT 'normal', -- low | normal | high | urgent
    intent_tag   VARCHAR(80),
    transcript   TEXT,
    resolution   TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tickets_customer ON tickets(customer_id);
CREATE INDEX idx_tickets_status   ON tickets(status);

-- Seed data for testing
INSERT INTO customers (name, email, phone, plan) VALUES
  ('Alice Sharma', 'alice@example.com', '+919900000001', 'pro'),
  ('Bob Kumar',   'bob@example.com',   '+919900000002', 'free'),
  ('Carol Singh', 'carol@example.com', '+919900000003', 'enterprise');
```

---

### Step 3 — Build the CRM MCP Server

**`mcp_servers/crm_mcp/db.py`** — connection pool:

```python
# db.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"].replace(
    "postgresql://", "postgresql+asyncpg://"
)
engine      = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

**`mcp_servers/crm_mcp/server.py`** — CRM tools:

```python
# server.py — CRM MCP server
from mcp.server.fastmcp import FastMCP
from sqlalchemy import text
from .db import AsyncSessionLocal
from loguru import logger
import os, json

mcp  = FastMCP("crm-server")
PORT = int(os.getenv("CRM_MCP_PORT", 8001))


@mcp.tool()
async def get_customer(email: str) -> str:
    """Look up a customer record by email address."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, name, email, plan FROM customers WHERE email = :email"),
            {"email": email},
        )
        row = result.fetchone()
    if not row:
        return json.dumps({"found": False})
    return json.dumps({"found": True, "id": str(row.id),
                       "name": row.name, "plan": row.plan})


@mcp.tool()
async def lookup_open_tickets(customer_id: str) -> str:
    """Return all open tickets for a customer ID."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""SELECT id, subject, status, priority, created_at
                    FROM tickets WHERE customer_id = :cid AND status = 'open'
                    ORDER BY created_at DESC LIMIT 5"""),
            {"cid": customer_id},
        )
        rows = result.fetchall()
    tickets = [{"id": str(r.id), "subject": r.subject,
                "priority": r.priority} for r in rows]
    return json.dumps({"tickets": tickets, "count": len(tickets)})


@mcp.tool()
async def create_ticket(customer_id: str, subject: str,
                        intent_tag: str, transcript: str,
                        priority: str = "normal") -> str:
    """Create a support ticket (used by Escalator on handoff)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""INSERT INTO tickets
                    (customer_id, subject, intent_tag, transcript, priority, status)
                    VALUES (:cid, :sub, :tag, :trans, :pri, 'escalated')
                    RETURNING id"""),
            {"cid": customer_id, "sub": subject, "tag": intent_tag,
             "trans": transcript, "pri": priority},
        )
        ticket_id = str(result.fetchone().id)
        await session.commit()
    logger.info(f"Ticket created | id={ticket_id}")
    return json.dumps({"ticket_id": ticket_id, "status": "escalated"})


@mcp.tool()
async def resolve_ticket(ticket_id: str, resolution: str) -> str:
    """Mark a ticket as resolved with a resolution note."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""UPDATE tickets SET status='resolved', resolution=:res,
                    updated_at=NOW() WHERE id=:tid"""),
            {"tid": ticket_id, "res": resolution},
        )
        await session.commit()
    return json.dumps({"ticket_id": ticket_id, "status": "resolved"})


if __name__ == "__main__":
    mcp.run(transport="sse", port=PORT)
```

---

### Step 4 — Build the Email MCP Server

```python
# mcp_servers/email_mcp/server.py
from mcp.server.fastmcp import FastMCP
import aiosmtplib
from email.message import EmailMessage
from loguru import logger
import os, json

mcp  = FastMCP("email-server")
PORT = int(os.getenv("EMAIL_MCP_PORT", 8002))


@mcp.tool()
async def send_confirmation_email(to_email: str, customer_name: str,
                                  subject: str, body: str) -> str:
    """Send a support confirmation or follow-up email to a customer."""
    msg             = EmailMessage()
    msg["From"]     = os.environ["EMAIL_FROM"]
    msg["To"]       = to_email
    msg["Subject"]  = subject
    msg.set_content(body)
    try:
        await aiosmtplib.send(
            msg,
            hostname=os.environ["SMTP_HOST"],
            port=int(os.environ["SMTP_PORT"]),
            username=os.environ["SMTP_USER"],
            password=os.environ["SMTP_PASSWORD"],
            start_tls=True,
        )
        logger.info(f"Email sent | to={to_email}")
        return json.dumps({"sent": True, "to": to_email})
    except Exception as e:
        logger.error(f"Email failed | {e}")
        return json.dumps({"sent": False, "error": str(e)})


if __name__ == "__main__":
    mcp.run(transport="sse", port=PORT)
```

---

### Step 5 — Shared Call Context

```python
# agent/context.py — dataclass passed between all three agents
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CallContext:
    session_id:       str
    transcript:       list[str]        = field(default_factory=list)
    customer_email:   Optional[str]    = None
    customer_id:      Optional[str]    = None
    customer_name:    Optional[str]    = None
    intent_tag:       Optional[str]    = None
    intent_conf:      float            = 0.0
    answer_conf:      float            = 0.0
    tool_attempts:    list[str]        = field(default_factory=list)
    escalation_reason: Optional[str]  = None

    def add_turn(self, speaker: str, text: str):
        self.transcript.append(f"{speaker}: {text}")

    def full_transcript(self) -> str:
        return "\n".join(self.transcript)
```

---

### Step 6 — Confidence Gate

```python
# agent/confidence_gate.py — the decision engine
import os
from loguru import logger

AUTO_RESOLVE = float(os.getenv("CONFIDENCE_AUTO_RESOLVE", 0.85))
CLARIFY_MIN  = float(os.getenv("CONFIDENCE_CLARIFY", 0.60))


def gate(ctx) -> str:
    """
    Returns one of: 'AUTO_RESOLVE' | 'CLARIFY' | 'ESCALATE'
    Uses the weakest link (min of intent + answer confidence).
    Hard override always wins.
    """
    # Hard override: caller explicitly asked for human
    if ctx.escalation_reason == "user_requested":
        logger.info("Gate | ESCALATE (user requested)")
        return "ESCALATE"

    score = min(ctx.intent_conf, ctx.answer_conf)
    logger.info(f"Gate | intent={ctx.intent_conf:.2f} "
                f"answer={ctx.answer_conf:.2f} combined={score:.2f}")

    if score >= AUTO_RESOLVE:
        return "AUTO_RESOLVE"
    if score >= CLARIFY_MIN:
        return "CLARIFY"
    return "ESCALATE"
```

---

### Step 7 — Classifier Agent

```python
# agent/classifier.py — tags intent + emits confidence
import json, os
from groq import AsyncGroq
from .context import CallContext
from loguru import logger

INTENT_LABELS = [
    "billing_refund", "billing_upgrade", "billing_cancel",
    "account_password_reset", "account_login_issue",
    "order_status", "order_cancel", "order_return",
    "technical_bug", "technical_setup",
    "general_inquiry", "other",
]

CLASSIFIER_PROMPT = f"""
You are a support ticket classifier. Given a customer's message, output ONLY
valid JSON with these exact fields:
{{
  "intent": "<one of: {', '.join(INTENT_LABELS)}>",
  "confidence": <float 0.0-1.0>,
  "entities": {{
    "email": "<if mentioned or null>",
    "order_id": "<if mentioned or null>",
    "product": "<if mentioned or null>"
  }}
}}
No explanation. No markdown. JSON only.
"""

_client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])


async def classify(user_message: str, ctx: CallContext) -> CallContext:
    """Run the classifier and update ctx with intent + confidence."""
    resp = await _client.chat.completions.create(
        model=os.getenv("GROQ_CLASSIFIER_MODEL", "llama-3.1-8b-instant"),
        messages=[
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.0,
        max_tokens=200,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        parsed           = json.loads(raw)
        ctx.intent_tag   = parsed.get("intent", "other")
        ctx.intent_conf  = float(parsed.get("confidence", 0.0))
        entities         = parsed.get("entities", {})
        if entities.get("email"):
            ctx.customer_email = entities["email"]
        logger.info(f"Classified | intent={ctx.intent_tag} conf={ctx.intent_conf:.2f}")
    except json.JSONDecodeError:
        logger.error(f"Classifier JSON parse failed | raw={raw}")
        ctx.intent_tag  = "other"
        ctx.intent_conf = 0.0
    return ctx
```

---

### Step 8 — Escalator Agent

```python
# agent/escalator.py — wraps up and hands off to human
import json, redis.asyncio as redis, os
from .context import CallContext
from agent.tools import handle_tool_call
from loguru import logger

_redis = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


async def escalate(ctx: CallContext) -> str:
    """
    1. Create a ticket in CRM with full transcript.
    2. Store context in Redis for the human dashboard.
    3. Return a spoken farewell message.
    """
    # Create ticket
    ticket_result = await handle_tool_call("create_ticket", {
        "customer_id": ctx.customer_id or "unknown",
        "subject":     f"[{ctx.intent_tag}] Voice support escalation",
        "intent_tag":  ctx.intent_tag,
        "transcript":  ctx.full_transcript(),
        "priority":    "high" if ctx.intent_conf < 0.40 else "normal",
    })
    ticket = json.loads(ticket_result)

    # Store in Redis for human agent dashboard
    await _redis.setex(
        f"escalation:{ctx.session_id}",
        3600,  # 1 hour TTL
        json.dumps({
            "ticket_id":     ticket.get("ticket_id"),
            "customer":      ctx.customer_name,
            "intent":        ctx.intent_tag,
            "transcript":    ctx.full_transcript(),
            "reason":        ctx.escalation_reason,
        }),
    )
    logger.info(f"Escalated | session={ctx.session_id} ticket={ticket.get('ticket_id')}")

    return (
        "I am connecting you with a human agent right now. "
        "They will have the full details of our conversation, "
        "so you won't need to repeat anything. Please hold."
    )
```

---

### Step 9 — Updated Pipeline with Pipecat Flows

```python
# agent/flows/support_flow.py — dialog state machine
from pipecat.flows.manager import FlowManager
from pipecat.flows.types import FlowConfig, NodeConfig, EdgeConfig

SUPPORT_FLOW: FlowConfig = FlowConfig(
    initial_node="greet",
    nodes=[
        NodeConfig(
            id="greet",
            task_messages=[{
                "role": "system",
                "content": "Greet the caller warmly and ask how you can help."
            }],
            actions=[{"type": "tts_say", "text":
                "Hello! Thank you for calling support. How can I help you today?"}],
            edges=[EdgeConfig(target="understand", condition="user_spoke")],
        ),
        NodeConfig(
            id="understand",
            task_messages=[{
                "role": "system",
                "content": "Listen to the problem. Call search_kb if it's a how-to question. "
                           "Call get_customer if they mention their email."
            }],
            edges=[
                EdgeConfig(target="resolve",   condition="confidence_high"),
                EdgeConfig(target="clarify",   condition="confidence_medium"),
                EdgeConfig(target="escalate",  condition="confidence_low"),
                EdgeConfig(target="escalate",  condition="human_requested"),
            ],
        ),
        NodeConfig(
            id="clarify",
            task_messages=[{"role": "system",
                "content": "Ask ONE clarifying question to better understand the issue."}],
            edges=[EdgeConfig(target="understand", condition="user_spoke")],
        ),
        NodeConfig(
            id="resolve",
            task_messages=[{"role": "system",
                "content": "Resolve the issue using tools. Confirm with the customer."}],
            edges=[
                EdgeConfig(target="close",    condition="resolved"),
                EdgeConfig(target="escalate", condition="tool_failed"),
            ],
        ),
        NodeConfig(
            id="escalate",
            task_messages=[{"role": "system",
                "content": "Inform the customer you are transferring them. Be warm."}],
            actions=[{"type": "run_function", "function": "escalate"}],
            edges=[],  # terminal node
        ),
        NodeConfig(
            id="close",
            task_messages=[{"role": "system",
                "content": "Thank the customer and close the call."}],
            actions=[{"type": "tts_say",
                "text": "Is there anything else I can help you with today?"}],
            edges=[],  # terminal node
        ),
    ],
)
```

---

## Testing / Validation

```python
# tests/test_confidence_gate.py
from agent.context import CallContext
from agent.confidence_gate import gate

def test_auto_resolve():
    ctx = CallContext(session_id="t1", intent_conf=0.92, answer_conf=0.91)
    assert gate(ctx) == "AUTO_RESOLVE"

def test_clarify():
    ctx = CallContext(session_id="t2", intent_conf=0.75, answer_conf=0.80)
    assert gate(ctx) == "CLARIFY"

def test_escalate_low_conf():
    ctx = CallContext(session_id="t3", intent_conf=0.40, answer_conf=0.90)
    assert gate(ctx) == "ESCALATE"

def test_user_override():
    ctx = CallContext(session_id="t4", intent_conf=0.99, answer_conf=0.99,
                      escalation_reason="user_requested")
    assert gate(ctx) == "ESCALATE"
```

```bash
# run all tests
pytest tests/ -v --asyncio-mode=auto
```

---

## Definition of Done — Phase 2 ✅

- [ ] Postgres starts via Docker Compose with schema + seed data applied
- [ ] Redis starts and accepts connections
- [ ] CRM MCP server: all 4 tools respond correctly (unit tested)
- [ ] Email MCP server: sends a real test email
- [ ] Classifier: returns valid JSON with intent + confidence for 10 test phrases
- [ ] Confidence gate: correctly routes AUTO_RESOLVE / CLARIFY / ESCALATE in unit tests
- [ ] Escalator: creates a ticket in Postgres and stores context in Redis
- [ ] Full call demo: ask a billing question → classify → resolve with CRM → confirmation email sent
- [ ] Full call demo: say "I want a human" → escalate within 1 turn → ticket created

---

*Next: Phase 3 — add real phone lines (LiveKit + SIP), OpenTelemetry tracing, Prometheus metrics, Nginx, and a human handoff dashboard.*
