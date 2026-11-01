"""
api/v1/health.py

Liveness check endpoint — no auth, no DB access.

Always returns HTTP 200 as long as the process is running. NPM and monitoring
systems use this to detect whether the container is reachable.
No auth so external health checks work without a token.

Deeper checks (DB connection, Redis) are deliberately omitted here —
the endpoint should always respond quickly and only report real container failure.
"""
from datetime import datetime, UTC
from fastapi import APIRouter
from pydantic import BaseModel, Field
from api.config import settings

router = APIRouter()


class HealthOut(BaseModel):
    """The liveness answer, typed (CHY-28).

    It was `-> dict`, which means the published contract said "an object, who
    knows with what in it" for the one endpoint every monitoring system calls.
    Blackbox probes and Traefik health checks are clients too.
    """

    status: str = Field(..., examples=["ok"])
    version: str = Field(..., examples=["0.1.0"])
    timestamp: datetime


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(
        status="ok",
        version=settings.api_version,
        timestamp=datetime.now(UTC),
    )
