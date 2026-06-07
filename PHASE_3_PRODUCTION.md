# Phase 3 — Production Hardening: Telephony + Observability + Deploy

> **Goal:** Take the Phase 2 triage system from a demo to something that survives real inbound calls — real phone lines, full distributed tracing, latency optimization, a human handoff dashboard, containerized deployment, and an eval suite.

---

## Overview

This phase adds no new agent logic. It makes everything that already works **reliable, observable, and scalable**. By the end you have:
- A real phone number customers can call (LiveKit + SIP)
- Every hop traced with OpenTelemetry (spans, confidence scores, tool durations)
- Prometheus + Grafana dashboards
- A FastAPI human-handoff dashboard for the support team
- Full Docker Compose orchestration of every service
- An automated eval suite that measures resolve rate and escalation accuracy

---

## New Production Stack

| Tool | Version | Role | Cost |
|------|---------|------|------|
| **LiveKit** | 1.7+ | WebRTC SFU (self-hosted Go server) | Free (Apache 2.0) |
| **livekit-agents** | 1.x | Pipecat ↔ LiveKit bridge for agents | Free |
| **Twilio** | — | SIP trunk → real phone number | ~$0.0085/min inbound |
| **OpenTelemetry SDK** | 1.x | Distributed tracing across all services | Free |
| **opentelemetry-exporter-otlp** | — | Ship traces to Jaeger/Tempo | Free |
| **Jaeger** | 1.x | Trace viewer (self-hosted) | Free (Apache 2.0) |
| **Prometheus** | 2.x | Metrics collection | Free (Apache 2.0) |
| **Grafana** | 10+ | Metrics dashboard | Free (AGPL) |
| **FastAPI** | 0.115+ | Human handoff dashboard API | Free |
| **Nginx** | 1.26 | Reverse proxy + TLS termination | Free |
| **Certbot** | — | Free TLS certs (Let's Encrypt) | Free |

All Phase 1 + 2 dependencies still apply.

---

## API Keys Required

| Key | Where to get | Cost |
|-----|-------------|------|
| `GROQ_API_KEY` | From Phase 1 | Free tier |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Generated locally via `livekit-server` | Free (self-hosted) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | https://twilio.com/console | ~$0.0085/min inbound calls |
| `TWILIO_PHONE_NUMBER` | Twilio Console → Buy a number | ~$1.15/month |

Twilio is the **only new paid item** in Phase 3, and it's optional — skip it if you want to keep testing via browser mic.

---

## Updated Project Structure

```
voice-support-triage/
├── docker-compose.yml            # full orchestration (all services)
├── nginx/
│   ├── nginx.conf                # reverse proxy config
│   └── certs/                    # TLS certs (certbot or self-signed)
│
├── livekit/
│   ├── livekit.yaml              # LiveKit server config
│   └── Dockerfile                # optional: custom LiveKit build
│
├── agent/
│   ├── transport/
│   │   └── livekit_transport.py  # NEW: LiveKit transport adapter
│   └── pipeline.py               # updated: use LiveKit transport
│
├── observability/
│   ├── tracing.py                # OpenTelemetry tracer setup
│   ├── metrics.py                # Prometheus metrics
│   └── middleware.py             # FastAPI OTEL middleware
│
├── dashboard/
│   ├── main.py                   # FastAPI human-handoff dashboard
│   ├── templates/
│   │   └── queue.html            # real-time escalation queue
│   └── static/
│
├── evals/
│   ├── test_cases.json           # 50 annotated call scenarios
│   ├── run_evals.py              # eval runner (headless)
│   └── report.py                 # generate eval report
│
├── prometheus/
│   └── prometheus.yml            # scrape config
│
├── grafana/
│   └── dashboards/
│       └── voice_triage.json     # pre-built Grafana dashboard
│
└── scripts/
    ├── generate_livekit_token.py
    └── simulate_call.py          # headless call simulation for evals
```

---

## Environment Variables (additions to Phase 2)

```bash
# .env — Phase 3 additions

# ── LiveKit ────────────────────────────────────────────────
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret

# ── Twilio SIP Trunk ───────────────────────────────────────
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx

# ── Observability ─────────────────────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=voice-triage-agent
PROMETHEUS_PORT=9090

# ── Human Handoff Dashboard ───────────────────────────────
DASHBOARD_PORT=8080
DASHBOARD_SECRET_KEY=changeme_in_prod

# ── Environment ───────────────────────────────────────────
ENV=production      # or: development
LOG_LEVEL=INFO
```

---

## Step-by-Step Implementation

### Step 1 — Set Up LiveKit Server

```yaml
# livekit/livekit.yaml
port: 7880
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end:   50200
keys:
  devkey: devsecret    # change in production
logging:
  level: info
```

```bash
# install LiveKit server (Go binary)
curl -sSL https://get.livekit.io | bash

# start LiveKit
livekit-server --config ./livekit/livekit.yaml

# generate a test token
pip install livekit
python scripts/generate_livekit_token.py
```

```python
# scripts/generate_livekit_token.py
from livekit import api
import os

token = (
    api.AccessToken(
        os.environ["LIVEKIT_API_KEY"],
        os.environ["LIVEKIT_API_SECRET"],
    )
    .with_identity("test-agent")
    .with_name("Test Agent")
    .with_grants(api.VideoGrants(room_join=True, room="support-room"))
    .to_jwt()
)
print(f"Token: {token}")
```

---

### Step 2 — LiveKit Transport for Pipecat

```python
# agent/transport/livekit_transport.py
# Replaces LocalAudioTransport from Phase 1 for production calls

import os
from livekit.agents import WorkerOptions, cli
from pipecat.transports.services.livekit import LiveKitTransport, LiveKitParams

LIVEKIT_URL    = os.environ["LIVEKIT_URL"]
LIVEKIT_TOKEN  = os.environ.get("LIVEKIT_TOKEN", "")


def build_livekit_transport() -> LiveKitTransport:
    """Production transport: replaces LocalAudioTransport."""
    return LiveKitTransport(
        url=LIVEKIT_URL,
        token=LIVEKIT_TOKEN,
        room_name="support-room",
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
        ),
    )
```

**Update `agent/pipeline.py`:** swap `LocalAudioTransport` for `build_livekit_transport()` with no other pipeline changes.

---

### Step 3 — Connect Twilio SIP Trunk

Twilio SIP bridges the public phone network into your LiveKit room.

```
Phone → Twilio SIP → LiveKit SIP server → LiveKit Room → Pipecat Agent
```

**In Twilio Console:**
1. Elastic SIP Trunking → Create a new trunk
2. Set origination URI: `sip:<YOUR_LIVEKIT_HOST>:5060`
3. Buy a phone number → assign it to the trunk
4. Set inbound webhook: `https://yourserver.com/twilio/incoming`

```python
# dashboard/main.py — Twilio inbound webhook handler
from fastapi import FastAPI, Form
from fastapi.responses import Response
import livekit.api as lkapi
import os

app = FastAPI()
lk  = lkapi.LiveKitAPI(
    os.environ["LIVEKIT_URL"],
    os.environ["LIVEKIT_API_KEY"],
    os.environ["LIVEKIT_API_SECRET"],
)

@app.post("/twilio/incoming")
async def handle_incoming_call(From: str = Form(...), CallSid: str = Form(...)):
    """
    Twilio calls this when a real call comes in.
    We create a LiveKit room token and redirect SIP there.
    """
    token = (
        lkapi.AccessToken(
            os.environ["LIVEKIT_API_KEY"],
            os.environ["LIVEKIT_API_SECRET"],
        )
        .with_identity(f"caller-{CallSid}")
        .with_name(f"Caller {From}")
        .with_grants(lkapi.VideoGrants(room_join=True, room=f"call-{CallSid}"))
        .to_jwt()
    )
    # TwiML response: redirect SIP into LiveKit
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:{os.environ['LIVEKIT_URL'].replace('ws://', '')}?token={token}</Sip>
  </Dial>
</Response>"""
    return Response(content=twiml, media_type="application/xml")
```

---

### Step 4 — OpenTelemetry Tracing

```python
# observability/tracing.py — global tracer setup
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
import os

_tracer: trace.Tracer | None = None


def setup_tracing() -> trace.Tracer:
    global _tracer
    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME",
                                                            "voice-triage")})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT",
                                                    "http://localhost:4317"))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("voice-triage.agent")
    return _tracer


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return setup_tracing()
    return _tracer
```

**Instrument the confidence gate** — every decision gets a span:

```python
# agent/confidence_gate.py — updated with tracing
from observability.tracing import get_tracer

def gate(ctx) -> str:
    tracer = get_tracer()
    with tracer.start_as_current_span("confidence_gate") as span:
        span.set_attribute("session.id",        ctx.session_id)
        span.set_attribute("intent.tag",         ctx.intent_tag or "none")
        span.set_attribute("intent.confidence",  ctx.intent_conf)
        span.set_attribute("answer.confidence",  ctx.answer_conf)

        if ctx.escalation_reason == "user_requested":
            span.set_attribute("gate.decision", "ESCALATE_OVERRIDE")
            return "ESCALATE"

        score = min(ctx.intent_conf, ctx.answer_conf)
        span.set_attribute("gate.combined_score", score)

        if score >= float(os.getenv("CONFIDENCE_AUTO_RESOLVE", 0.85)):
            span.set_attribute("gate.decision", "AUTO_RESOLVE")
            return "AUTO_RESOLVE"
        if score >= float(os.getenv("CONFIDENCE_CLARIFY", 0.60)):
            span.set_attribute("gate.decision", "CLARIFY")
            return "CLARIFY"

        span.set_attribute("gate.decision", "ESCALATE")
        return "ESCALATE"
```

---

### Step 5 — Prometheus Metrics

```python
# observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge, start_http_server
import os

# counters
calls_total       = Counter("vt_calls_total", "Total inbound calls")
calls_resolved    = Counter("vt_calls_resolved_total", "Auto-resolved calls")
calls_escalated   = Counter("vt_calls_escalated_total", "Escalated calls")
tool_calls_total  = Counter("vt_tool_calls_total", "MCP tool invocations",
                             ["tool_name"])

# histograms
call_duration_sec = Histogram("vt_call_duration_seconds",
                               "Total call duration", buckets=[10, 30, 60, 120, 300])
ttfr_seconds      = Histogram("vt_time_to_first_response_seconds",
                               "STT → LLM first token latency",
                               buckets=[0.3, 0.5, 0.8, 1.2, 2.0, 5.0])
conf_score        = Histogram("vt_confidence_score", "Gate confidence scores",
                               buckets=[0.1, 0.3, 0.5, 0.6, 0.7, 0.85, 0.95, 1.0])

# gauges
active_calls = Gauge("vt_active_calls", "Calls in progress")


def start_metrics_server():
    port = int(os.getenv("PROMETHEUS_PORT", 9090))
    start_http_server(port)
```

---

### Step 6 — Human Handoff Dashboard

```python
# dashboard/main.py — escalation queue for support team
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import redis.asyncio as redis
import json, os

app    = FastAPI(title="Voice Triage — Human Queue")
_redis = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


@app.get("/queue", response_class=HTMLResponse)
async def queue_page():
    """Returns the real-time escalation queue page."""
    with open("dashboard/templates/queue.html") as f:
        return HTMLResponse(f.read())


@app.get("/api/escalations")
async def list_escalations():
    """List all pending escalations from Redis."""
    keys  = await _redis.keys("escalation:*")
    items = []
    for key in keys:
        raw = await _redis.get(key)
        if raw:
            items.append(json.loads(raw))
    items.sort(key=lambda x: x.get("ticket_id", ""))
    return {"escalations": items, "count": len(items)}


@app.delete("/api/escalations/{session_id}")
async def claim_escalation(session_id: str):
    """Agent claims a ticket — removes it from the queue."""
    await _redis.delete(f"escalation:{session_id}")
    return {"claimed": True, "session_id": session_id}


@app.websocket("/ws/queue")
async def queue_ws(ws: WebSocket):
    """Push new escalations to the dashboard in real time."""
    await ws.accept()
    pubsub = _redis.pubsub()
    await pubsub.subscribe("escalations")
    async for msg in pubsub.listen():
        if msg["type"] == "message":
            await ws.send_text(msg["data"])
```

---

### Step 7 — Full Docker Compose Orchestration

```yaml
# docker-compose.yml — production (all services)
version: "3.9"

services:
  # ── Voice pipeline ─────────────────────────────────────
  agent:
    build: .
    command: python agent/pipeline.py
    env_file: .env
    depends_on: [postgres, redis, kokoro-tts, livekit]
    restart: unless-stopped

  kokoro-tts:
    image: ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2
    ports: ["8880:8880"]
    restart: unless-stopped

  # ── MCP Servers ────────────────────────────────────────
  kb-mcp:
    build: .
    command: python -m mcp_servers.kb_mcp.server
    env_file: .env
    volumes: ["./chroma_store:/app/chroma_store"]
    restart: unless-stopped

  crm-mcp:
    build: .
    command: python -m mcp_servers.crm_mcp.server
    env_file: .env
    depends_on: [postgres]
    restart: unless-stopped

  email-mcp:
    build: .
    command: python -m mcp_servers.email_mcp.server
    env_file: .env
    restart: unless-stopped

  # ── Data stores ────────────────────────────────────────
  postgres:
    image: postgres:16-alpine
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./mcp_servers/crm_mcp/schema.sql:/docker-entrypoint-initdb.d/schema.sql
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    restart: unless-stopped

  # ── Transport ──────────────────────────────────────────
  livekit:
    image: livekit/livekit-server:latest
    volumes: ["./livekit/livekit.yaml:/etc/livekit.yaml"]
    command: --config /etc/livekit.yaml
    ports: ["7880:7880", "7881:7881", "50000-50200:50000-50200/udp"]
    restart: unless-stopped

  # ── Human dashboard ────────────────────────────────────
  dashboard:
    build: .
    command: uvicorn dashboard.main:app --host 0.0.0.0 --port 8080
    env_file: .env
    depends_on: [redis]
    restart: unless-stopped

  # ── Observability ──────────────────────────────────────
  jaeger:
    image: jaegertracing/all-in-one:1.58
    ports: ["16686:16686", "4317:4317"]
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.52.0
    volumes: ["./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml"]
    ports: ["9091:9090"]
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.4.0
    volumes: ["./grafana/dashboards:/var/lib/grafana/dashboards"]
    ports: ["3000:3000"]
    depends_on: [prometheus]
    restart: unless-stopped

  # ── Reverse proxy ──────────────────────────────────────
  nginx:
    image: nginx:1.26-alpine
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
      - ./nginx/certs:/etc/nginx/certs
    ports: ["80:80", "443:443"]
    depends_on: [agent, dashboard]
    restart: unless-stopped

volumes:
  pgdata:
```

---

### Step 8 — Nginx Reverse Proxy

```nginx
# nginx/nginx.conf
events { worker_connections 1024; }

http {
  upstream dashboard { server dashboard:8080; }
  upstream grafana    { server grafana:3000; }

  server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
  }

  server {
    listen 443 ssl;
    server_name yourdomain.com;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    location /dashboard/ {
      proxy_pass http://dashboard/;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
    }

    location /grafana/ {
      proxy_pass http://grafana/;
    }
  }
}
```

---

### Step 9 — Eval Suite

```json
// evals/test_cases.json — sample annotated scenarios
[
  {
    "id": "tc001",
    "input": "How do I reset my password?",
    "expected_intent": "account_password_reset",
    "expected_outcome": "AUTO_RESOLVE",
    "expected_tool_called": "search_kb"
  },
  {
    "id": "tc002",
    "input": "I want to cancel my subscription",
    "expected_intent": "billing_cancel",
    "expected_outcome": "AUTO_RESOLVE",
    "expected_tool_called": "get_customer"
  },
  {
    "id": "tc003",
    "input": "adfkjadfkjadf",
    "expected_intent": "other",
    "expected_outcome": "ESCALATE"
  },
  {
    "id": "tc004",
    "input": "Let me talk to a real person",
    "expected_intent": "other",
    "expected_outcome": "ESCALATE"
  }
]
```

```python
# evals/run_evals.py — headless eval runner
import asyncio, json
from agent.classifier import classify
from agent.confidence_gate import gate
from agent.context import CallContext
import uuid

async def run_eval(case: dict) -> dict:
    ctx = CallContext(session_id=str(uuid.uuid4()))
    ctx = await classify(case["input"], ctx)
    # mock answer confidence for eval
    ctx.answer_conf = 0.90 if ctx.intent_tag != "other" else 0.30
    decision = gate(ctx)
    passed = (
        ctx.intent_tag == case["expected_intent"] and
        decision == case["expected_outcome"]
    )
    return {"id": case["id"], "passed": passed,
            "intent": ctx.intent_tag, "decision": decision,
            "expected_intent": case["expected_intent"],
            "expected_outcome": case["expected_outcome"]}

async def main():
    cases   = json.load(open("evals/test_cases.json"))
    results = await asyncio.gather(*[run_eval(c) for c in cases])
    passed  = sum(1 for r in results if r["passed"])
    print(f"\n{'='*50}")
    print(f"Eval Results: {passed}/{len(results)} passed")
    print(f"{'='*50}\n")
    for r in results:
        status = "✅" if r["passed"] else "❌"
        print(f"{status} {r['id']} | intent: {r['intent']} | "
              f"decision: {r['decision']}")

if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python evals/run_evals.py
```

---

## Key Metrics to Monitor in Grafana

| Metric | Target | Alert if |
|--------|--------|---------|
| `vt_calls_resolved_total / vt_calls_total` | > 70% | < 50% |
| `vt_time_to_first_response_seconds p95` | < 1.5s | > 3s |
| `vt_confidence_score` histogram | peak > 0.85 | many calls < 0.60 |
| `vt_calls_escalated_total` | < 30% of calls | > 50% |
| `vt_tool_calls_total{tool="search_kb"}` | — | sudden drop (KB down?) |

---

## Security Checklist

- [ ] All secrets in `.env`, never hardcoded, never committed
- [ ] Postgres: strong password, not exposed to public internet
- [ ] Redis: bind to `127.0.0.1` or Docker network only (no public port)
- [ ] Nginx: TLS on all public endpoints (Certbot / Let's Encrypt)
- [ ] LiveKit: rotate API key + secret before production
- [ ] Twilio: IP ACL on SIP trunk (whitelist your server IP)
- [ ] Rate limit the `/twilio/incoming` webhook in Nginx
- [ ] Validate Twilio webhook signatures in production

---

## Definition of Done — Phase 3 ✅

- [ ] `docker-compose up` starts all services cleanly in one command
- [ ] LiveKit room accepts connections (test with browser at https://meet.livekit.io)
- [ ] Twilio SIP trunk routes a real test call into LiveKit room
- [ ] Call is picked up by Pipecat agent via LiveKit transport
- [ ] Jaeger UI shows spans: STT → Classifier → Confidence Gate → Resolver → TTS
- [ ] Each span shows confidence scores as attributes
- [ ] Grafana dashboard shows live call volume and resolve rate
- [ ] Human dashboard shows escalations in real time
- [ ] Nginx serves dashboard + Grafana over HTTPS
- [ ] Eval suite passes ≥ 80% of annotated test cases
- [ ] All secrets rotated from dev values (devkey, changeme) to strong random strings

---

## What You've Built

```
Caller dials → Twilio SIP → LiveKit Room → Pipecat Agent
  │
  ├─ Silero VAD     (detects speech)
  ├─ Groq Whisper   (speech → text, $0.04/hr)
  ├─ Classifier     (intent + confidence, Groq 8B Instant)
  ├─ Confidence Gate → AUTO_RESOLVE / CLARIFY / ESCALATE
  ├─ Resolver       (calls KB-MCP, CRM-MCP, Email-MCP as needed)
  ├─ Escalator      (creates ticket, stores context in Redis)
  ├─ Kokoro TTS     (text → speech, self-hosted)
  └─ All traced via OpenTelemetry → Jaeger → Prometheus → Grafana

Human agent sees escalation queue in real time via dashboard.
```

**Total running cost at low volume:** ~$0.04–0.05/min of actual call time (Groq) + $0.0085/min (Twilio) = approximately $0.05/min per call. At zero calls, near-zero cost (only server hosting).
