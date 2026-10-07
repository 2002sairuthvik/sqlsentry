from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...engine import SQLSentry
from ...types import Feedback
from ..auth import Principal
from ..deps import audit, principal, sentry
from ..schemas import FeedbackRequest, GenerateResponse

router = APIRouter(prefix="/v1/generations", tags=["generations"])


@router.get("/{generation_id}", response_model=GenerateResponse)
def get_generation(
    generation_id: str, p: Principal = Depends(principal), s: SQLSentry = Depends(sentry)
) -> GenerateResponse:
    owner = None if p.consumer is None else p.name
    return GenerateResponse.from_generation(s.get_generation(generation_id, consumer=owner))


@router.post("/{generation_id}/feedback", response_model=Feedback, status_code=201)
def feedback(
    generation_id: str,
    body: FeedbackRequest,
    request: Request,
    p: Principal = Depends(principal),
    s: SQLSentry = Depends(sentry),
) -> Feedback:
    owner = None if p.consumer is None else p.name
    fb = s.feedback(
        generation_id, body.rating, corrected_sql=body.corrected_sql, comment=body.comment, consumer=owner
    )
    audit(request, "feedback", generation_id=generation_id, rating=body.rating)
    return fb
