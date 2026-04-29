# modules/llm/router.py

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session
from modules.llm.service import LLMService
from modules.llm.schemas import (
    SummaryCreateResponse,
    SummaryRead,
)

router = APIRouter(prefix="/llm", tags=["llm"])


@router.post(
    "/media/{media_id}/summaries",
    response_model=SummaryCreateResponse,
    status_code=201,
)
async def create_summary(
    media_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    service = LLMService(db)
    summary, job = await service.create_summary_job(
        user_id=current_user.user_id,
        media_id=media_id,
    )

    return SummaryCreateResponse(
        summary_id=summary.id,
        job_id=job.id,
        media_id=summary.media_id,
        user_id=summary.user_id,
        status=summary.status,
    )


@router.get(
    "/media/{media_id}/summaries",
    response_model=list[SummaryRead],
)
async def list_summaries_for_media(
    media_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    service = LLMService(db)
    return await service.list_user_summaries_for_media(
        user_id=current_user.user_id,
        media_id=media_id,
    )


@router.get(
    "/summaries/{summary_id}",
    response_model=SummaryRead,
)
async def get_summary(
    summary_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    service = LLMService(db)
    return await service.get_summary_for_user(
        user_id=current_user.user_id,
        summary_id=summary_id,
    )


@router.get("/jobs/{job_id}/status")
async def get_llm_job_status(
    job_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    service = LLMService(db)
    return await service.get_job_status(
        user_id=current_user.user_id,
        job_id=job_id,
    )
