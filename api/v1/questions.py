"""
api/v1/questions.py

HTTP for the question bank (CHY-23) — and nothing else.

GET /v1/questions        list, paged, optional topic filter
GET /v1/questions/{id}   one question

Read-only for API consumers. Questions are written by the offline ingest
script, pilots/study/scripts/ingest_questions.py.

`question_bank` belongs to the pilots context (ADR-030, CHY-104). This module
went on querying it directly until CHY-122 — the same oversight CHY-105 caught
for `context_blocks` and fixed there. Owning a table means owning the access to
it, not most of it.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.deps import require_auth
from api.v1._responses import NOT_FOUND
from contexts.pilots import service
from contexts.pilots.schema import QuestionOut
from core.db import get_pool
from kernel.schema import PG_INT_MAX

router = APIRouter()


@router.get("", response_model=list[QuestionOut])
async def list_questions(
    topic: str | None = Query(None, description="Filter by topic"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=PG_INT_MAX),
    _: None = Depends(require_auth),
    pool=Depends(get_pool),
) -> list[QuestionOut]:
    """Questions from the bank, newest first, optionally filtered by topic."""
    return await service.list_questions(pool, topic=topic, limit=limit, offset=offset)


@router.get("/{question_id}", response_model=QuestionOut, responses=NOT_FOUND)
async def get_question(
    question_id: UUID,
    _: None = Depends(require_auth),
    pool=Depends(get_pool),
) -> QuestionOut:
    return await service.get_question(pool, question_id)
