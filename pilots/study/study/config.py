import os
from dotenv import load_dotenv

load_dotenv()

# https, not http: the Traefik route answers plain HTTP with 404 rather than a
# redirect. One missing "s" cost three weeks of silent Langfuse traces (CHY-78);
# the same route serves LiteLLM, and an LLM call that 404s is not silent but is
# just as avoidable. Guarded by tests/test_internal_urls.py (CHY-96).
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "https://litellm.example.com/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
MODEL_SMALL = os.getenv("MODEL_SMALL", "phi3-mini")
MODEL_MEDIUM = os.getenv("MODEL_MEDIUM", "gemma4-e4b")

POSTGRES_URI = os.getenv("POSTGRES_URI", "")

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
# https, not http: the Traefik route answers plain HTTP with 404 rather than a
# redirect, and the Langfuse SDK swallows transport errors by design so the
# application keeps running. One missing "s" cost three weeks of silence --
# Langfuse received nothing between 2026-07-15 and 2026-08-07 (CHY-78).
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://langfuse.example.com")

MAX_QUALITY_ITERATIONS = 3
MAX_QUIZ_ITERATIONS = 2
CHUNK_SIZE = 1200
BATCH_SIZE = 10        # chunks per LLM call in map-reduce (10 × 1200 = ~12 000 chars)
MERGE_GROUP_SIZE = 5   # max summaries per merge call in hierarchical reduce
MIN_FACTS = 40         # quality-loop triggers if fewer facts were extracted

# CHY-23: question_bank coverage scoring
COVERAGE_THRESHOLD  = float(os.getenv("COVERAGE_THRESHOLD",  "0.90"))  # >= 90% covered → complete
QUESTION_BATCH_SIZE = int(os.getenv("QUESTION_BATCH_SIZE",   "50"))    # questions sampled per run
