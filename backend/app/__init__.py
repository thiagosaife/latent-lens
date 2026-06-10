"""LatentLens backend — real FastAPI service behind the AgentEvent SSE protocol."""

from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env (e.g. ANTHROPIC_API_KEY) before any Claude client is created.
# Existing environment variables take precedence (override=False).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

VERSION = "0.8.0"  # resumable SSE: decoupled runs, id-tagged frames, heartbeats, /stream + /cancel
