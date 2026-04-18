import os
import shutil
import tempfile
import bentoml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from modules.shared.celery import celery_app
from modules.media.models import Media, MediaSegment, MediaStatus
from modules.media.storage import s3_storage
from modules.media.config import media_config
from pydub import AudioSegment
from pydub.utils import make_chunks

engine = create_engine(media_config.database_url.replace("postgresql+asyncpg", "postgresql"))
SessionLocal = sessionmaker(bind=engine)
asr_client = bentoml.SyncHTTPClient("http://localhost:3000")


@celery_app.task(bind=True, name="asr.process_media")
def process_media_task(self, media_id_str: str):
    db = SessionLocal()
    tmp_dir = tempfile.mkdtemp()

    try:
        media = db.query(Media).filter(Media.id == media_id_str).first()
        if not media:
            return "Media not found"

        # СТАТУС: ПОДГОТОВКА
        media.status = MediaStatus.PREPARING
        db.commit()

        local_file = os.path.join(tmp_dir, "input_file")
        s3_storage.download_file(media.s3_key, local_file)

        audio = AudioSegment.from_file(local_file).set_channels(1).set_frame_rate(16000)
        chunks = make_chunks(audio, 30000)

        for i, chunk in enumerate(chunks):
            chunk_path = os.path.join(tmp_dir, f"chunk_{i}.wav")
            chunk.export(chunk_path, format="wav")
            new_seg = MediaSegment(media_id=media.id, position=i)
            db.add(new_seg)
        db.commit()

        # СТАТУС: ТРАНСКРИПЦИЯ
        media.status = MediaStatus.TRANSCRIBING
        db.commit()

        all_segments = db.query(MediaSegment).filter(MediaSegment.media_id == media_id_str).order_by(
            MediaSegment.position).all()
        final_texts = []

        for j in range(0, len(all_segments), 8):
            batch = all_segments[j:j + 8]
            paths = [os.path.join(tmp_dir, f"chunk_{seg.position}.wav") for seg in batch]

            results = asr_client.call("transcribe", paths=paths)

            for seg, res in zip(batch, results):
                seg.text = res["text"]
                final_texts.append(res["text"])

            db.commit()

            percent = int(((j + len(batch)) / len(all_segments)) * 100)
            # Отправляем инфо в RabbitMQ/Redis для фронта
            self.update_state(state='PROGRESS', meta={'stage': 'transcribing', 'percent': percent})

        # Записываем полный текст и завершаем
        media.full_text = " ".join(final_texts)
        media.status = MediaStatus.COMPLETED
        db.commit()

        return {"status": "SUCCESS"}

    except Exception as e:
        if 'media' in locals() and media:
            media.status = MediaStatus.FAILED
            db.commit()
        raise e
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
