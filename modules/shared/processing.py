# modules/shared/processing.py

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy.orm import Session

from modules.media.models import ProcessingJob, ProcessingStage


def utcnow():
    return datetime.now(UTC)


def clamp_progress(progress: int) -> int:
    return max(0, min(100, int(progress)))


def update_stage(
    db: Session,
    job_id,
    stage_name: str,
    progress: int,
    status: str = "processing",
    meta: Optional[dict] = None,
    error_message: Optional[str] = None,
):
    """
    Обновляет состояние job и конкретного stage.

    Это главный источник статуса для UI/WebSocket.
    Celery state больше не является источником правды.
    """
    progress = clamp_progress(progress)

    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        return None

    now = utcnow()

    if job.status == "pending":
        job.started_at = now

    job.status = "processing" if status in ("pending", "processing") else status
    job.current_stage = stage_name
    job.progress = progress

    stage = (
        db.query(ProcessingStage)
        .filter(
            ProcessingStage.job_id == job_id,
            ProcessingStage.stage_name == stage_name,
        )
        .first()
    )

    if not stage:
        stage = ProcessingStage(
            job_id=job_id,
            stage_name=stage_name,
            status=status,
            progress=progress,
            started_at=now,
            meta=meta,
        )
        db.add(stage)
    else:
        if stage.started_at is None:
            stage.started_at = now

        stage.status = status
        stage.progress = progress

        if meta is not None:
            stage.meta = meta

    if error_message:
        stage.error_message = error_message

    if status == "completed" or progress >= 100:
        stage.status = "completed"
        stage.progress = 100
        stage.completed_at = now

    if status == "failed":
        stage.status = "failed"
        job.status = "failed"
        job.error_message = error_message
        stage.error_message = error_message
        job.completed_at = now

    db.commit()
    return stage


def complete_job(db: Session, job_id):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        return None

    job.status = "completed"
    job.progress = 100
    job.completed_at = utcnow()
    db.commit()
    return job


def fail_job(db: Session, job_id, error_message: str):
    job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
    if not job:
        return None

    job.status = "failed"
    job.error_message = error_message
    job.completed_at = utcnow()
    db.commit()
    return job
