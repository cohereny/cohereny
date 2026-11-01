import os
from dotenv import load_dotenv

load_dotenv()

# https, not http: the Traefik route answers plain HTTP with 404 rather than a
# redirect. One missing "s" cost three weeks of silent Langfuse traces (CHY-78);
# the same route serves LiteLLM, and an LLM call that 404s is not silent but is
# just as avoidable. Guarded by tests/test_internal_urls.py (CHY-96).
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "https://litellm.example.com/v1")
LITELLM_API_KEY  = os.getenv("LITELLM_API_KEY", "dummy")

# Model tiers — routing reflects the data sensitivity split in the banking use case:
#   local: raw financial data stays on-premise (privacy_safe)
#   cloud: complex reasoning where latency matters for live demos
MODEL_SMALL  = os.getenv("MODEL_SMALL",  "phi3-mini")      # local, Raspberry Pi
MODEL_MEDIUM = os.getenv("MODEL_MEDIUM", "gemma4-e4b")     # local, Apple silicon
MODEL_CLOUD  = os.getenv("MODEL_CLOUD",  "gemma4-e4b")     # cloud (fallback: MODEL_MEDIUM until API keys configured)

POSTGRES_URI = os.getenv("POSTGRES_URI", "")

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
# A key of its own, and not the same as LANGFUSE_PUBLIC_KEY above: the
# CallbackHandler selects the Langfuse *project* by public key, and banking
# traces belong in the banking project. Read here rather than in graph.py so
# every configuration value of this pilot has one place (CHY-96).
BANKING_LANGFUSE_PUBLIC_KEY = os.getenv("BANKING_LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
# https, not http -- see the note in pilots/study/study/config.py (CHY-78).
LANGFUSE_HOST       = os.getenv("LANGFUSE_HOST", "https://langfuse.example.com")

# Workflow tuning
ANOMALY_THRESHOLD_YOY    = 0.30   # YoY change > 30% flags an anomaly
ANOMALY_THRESHOLD_CR     = 1.0    # Current Ratio below this is critical
ANOMALY_THRESHOLD_EQ     = 0.20   # EK-Quote below 20% is critical
