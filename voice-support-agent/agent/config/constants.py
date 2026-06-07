"""Project-wide constants and fixed strings.

These are values that don't change at runtime and aren't secrets, so they
live in code rather than in the environment.
"""

APP_NAME = "voice-support-agent"

# Spoken to the caller as soon as the voice session connects.
GREETING_MESSAGE = "Hello! I am your support assistant. How can I help you today?"

# Said when the knowledge base has no useful answer.
# Phase 1 has no human handoff yet — Phase 2 replaces this with a real escalation.
FALLBACK_MESSAGE = "I don't have that information. Let me connect you with a human agent."

# Help-doc file types the ingestion pipeline will pick up.
SUPPORTED_DOC_EXTENSIONS = (".txt", ".md")
