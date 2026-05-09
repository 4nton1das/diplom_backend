# modules/llm/service.py

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.media.models import (
    Media,
    UserMedia,
    MediaStatus,
    ProcessingJob,
    ProcessingStage,
)
from modules.llm.models import Summary, SummaryStatus


class LLMService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_user_has_media_access(
        self,
        user_id: uuid.UUID,
        media_id: uuid.UUID,
    ) -> Media:
        link_result = await self.db.execute(
            select(UserMedia).where(
                UserMedia.user_id == user_id,
                UserMedia.media_id == media_id,
            )
        )
        link = link_result.scalar_one_or_none()

        if not link:
            raise HTTPException(
                status_code=404,
                detail="Media not found or access denied",
            )

        media_result = await self.db.execute(
            select(Media).where(Media.id == media_id)
        )
        media = media_result.scalar_one_or_none()

        if not media:
            raise HTTPException(status_code=404, detail="Media not found")

        return media

    async def create_summary_job(
            self,
            user_id: uuid.UUID,
            media_id: uuid.UUID,
    ) -> tuple[Summary, ProcessingJob]:
        """
        Создает или перегенерирует пользовательский конспект.

        Логика:
        - один user + media = один актуальный Summary;
        - при перегенерации Summary переиспользуется;
        - создаётся новый ProcessingJob;
        - старый content очищается;
        - старые SummaryChunk удаляются;
        - новый результат LLM запишется в тот же Summary.
        """
        media = await self.check_user_has_media_access(user_id, media_id)

        if media.status != MediaStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail=f"ASR transcript is not ready. Current media status: {media.status.value}",
            )

        if not media.full_text:
            raise HTTPException(
                status_code=400,
                detail="ASR transcript is empty",
            )

        summary_result = await self.db.execute(
            select(Summary).where(
                Summary.user_id == user_id,
                Summary.media_id == media_id,
            )
        )
        summary = summary_result.scalar_one_or_none()

        job = ProcessingJob(
            media_id=media_id,
            user_id=user_id,
            job_type="summary",
            status="pending",
            progress=0,
        )
        self.db.add(job)
        await self.db.flush()

        if summary:
            summary.job_id = job.id
            summary.status = SummaryStatus.pending.value
            summary.content = None
            summary.content_json = None
            summary.title = None
            summary.model_name = None
            summary.provider = None
            summary.prompt_version = None
            summary.tokens_input = None
            summary.tokens_output = None
            summary.tokens_total = None
            summary.error_message = None
            summary.completed_at = None

            # Удаляем старые чанки этого summary.
            from modules.llm.models import SummaryChunk

            chunks_result = await self.db.execute(
                select(SummaryChunk).where(SummaryChunk.summary_id == summary.id)
            )
            old_chunks = chunks_result.scalars().all()

            for chunk in old_chunks:
                await self.db.delete(chunk)

        else:
            summary = Summary(
                media_id=media_id,
                user_id=user_id,
                job_id=job.id,
                status=SummaryStatus.pending.value,
                content=None,
                model_name=None,
                provider=None,
                prompt_version=None,
            )
            self.db.add(summary)

        self.db.add_all([
            ProcessingStage(
                job_id=job.id,
                stage_name="llm_map",
                status="pending",
                progress=0,
                meta={"message": "Waiting for LLM map stage"},
            ),
            ProcessingStage(
                job_id=job.id,
                stage_name="llm_reduce",
                status="pending",
                progress=0,
                meta={"message": "Waiting for LLM reduce stage"},
            ),
        ])

        await self.db.commit()
        await self.db.refresh(summary)
        await self.db.refresh(job)

        from modules.llm.tasks import process_summary_task
        process_summary_task.delay(str(job.id), str(summary.id))

        return summary, job

    async def get_summary_for_user(
        self,
        user_id: uuid.UUID,
        summary_id: uuid.UUID,
    ) -> Summary:
        from sqlalchemy.orm import selectinload

        result = await self.db.execute(
            select(Summary)
            .options(selectinload(Summary.media))
            .where(
                Summary.id == summary_id,
                Summary.user_id == user_id,
            )
        )
        summary = result.scalar_one_or_none()

        if not summary:
            raise HTTPException(
                status_code=404,
                detail="Summary not found",
            )

        return summary

    async def list_user_summaries_for_media(
        self,
        user_id: uuid.UUID,
        media_id: uuid.UUID,
    ) -> list[Summary]:
        from sqlalchemy.orm import selectinload
        await self.check_user_has_media_access(user_id, media_id)

        result = await self.db.execute(
            select(Summary)
            .options(selectinload(Summary.media))
            .where(
                Summary.user_id == user_id,
                Summary.media_id == media_id,
            )
            .order_by(Summary.created_at.desc())
        )

        return list(result.scalars().all())

    async def get_job_status(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> dict:
        job_result = await self.db.execute(
            select(ProcessingJob).where(
                ProcessingJob.id == job_id,
                ProcessingJob.user_id == user_id,
            )
        )
        job = job_result.scalar_one_or_none()

        if not job:
            raise HTTPException(
                status_code=404,
                detail="Processing job not found",
            )

        stages_result = await self.db.execute(
            select(ProcessingStage)
            .where(ProcessingStage.job_id == job.id)
            .order_by(ProcessingStage.created_at)
        )
        stages = stages_result.scalars().all()

        return {
            "id": job.id,
            "media_id": job.media_id,
            "user_id": job.user_id,
            "type": job.job_type,
            "status": job.status,
            "current_stage": job.current_stage,
            "progress": job.progress,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "stages": [
                {
                    "name": stage.stage_name,
                    "status": stage.status,
                    "progress": stage.progress,
                    "meta": stage.meta,
                    "error_message": stage.error_message,
                    "started_at": stage.started_at,
                    "completed_at": stage.completed_at,
                }
                for stage in stages
            ],
        }


async def get_llm_service(db: AsyncSession):
    return LLMService(db)
