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

engine = create_engine(media_config.database_url.replace("postgresql+asyncpg", "postgresql"))
SessionLocal = sessionmaker(bind=engine)
asr_client = bentoml.SyncHTTPClient("http://localhost:3000", timeout=600)


@celery_app.task(bind=True, name="asr.process_media")
def process_media_task(self, media_id_str: str):
    db = SessionLocal()
    tmp_dir = tempfile.mkdtemp()

    # Константы нарезки
    STEP_SEC = 40.0  # Длина логического сегмента
    OVERLAP_SEC = 2.0  # Нахлест, чтобы не разрезать слово пополам

    try:
        media = db.query(Media).filter(Media.id == media_id_str).first()
        if not media:
            return "Media not found"

        media.status = MediaStatus.PREPARING
        db.commit()

        # 1. ЗАГРУЗКА И ПОДГОТОВКА АУДИО
        raw_file = os.path.join(tmp_dir, "raw_input")
        mono_file = os.path.join(tmp_dir, "mono_input.wav")
        s3_storage.download_file(media.s3_key, raw_file)

        audio = AudioSegment.from_file(raw_file).set_channels(1).set_frame_rate(16000)
        audio.export(mono_file, format="wav")
        duration_sec = len(audio) / 1000.0

        # 2. НАРЕЗКА С НАХЛЕСТОМ (OVERLAP)
        current_start = 0.0
        idx = 0
        while current_start < duration_sec:
            start_t = current_start
            end_t = min(start_t + STEP_SEC, duration_sec)
            # Реальный конец файла для ASR (с запасом)
            actual_end_t = min(end_t + OVERLAP_SEC, duration_sec)

            chunk = audio[start_t * 1000: actual_end_t * 1000]
            chunk_path = os.path.join(tmp_dir, f"chunk_{idx}.wav")
            chunk.export(chunk_path, format="wav")

            db.add(MediaSegment(
                media_id=media.id,
                position=idx,
                start_time=start_t,
                end_time=end_t,
                text=""
            ))

            current_start += STEP_SEC
            idx += 1
        db.commit()

        # 3. ТРАНСКРИПЦИЯ (Batch processing)
        media.status = MediaStatus.TRANSCRIBING
        db.commit()

        all_segments = db.query(MediaSegment).filter(MediaSegment.media_id == media_id_str).order_by(
            MediaSegment.position).all()
        global_words = []

        for j in range(0, len(all_segments), 8):
            batch = all_segments[j:j + 8]
            paths = [os.path.join(tmp_dir, f"chunk_{seg.position}.wav") for seg in batch]

            results = asr_client.call("transcribe", paths=paths)

            for seg, res in zip(batch, results):
                seg_offset = seg.start_time
                segment_valid_words = []

                for w in res.get("words", []):
                    # Вычисляем глобальное время слова в контексте всего файла
                    abs_start = w["start"] + seg_offset
                    abs_end = w["end"] + seg_offset

                    # ФИЛЬТРАЦИЯ: Берем слово только если оно началось в границах текущего сегмента.
                    # Если слово началось на 40.5 сек (в зоне нахлеста), мы проигнорируем его здесь
                    # и возьмем его в следующем сегменте, где оно будет считаться "своим".
                    if seg.start_time <= abs_start < seg.end_time:
                        word_obj = {
                            "word": w["word"],
                            "start": round(abs_start, 2),
                            "end": round(abs_end, 2)
                        }
                        segment_valid_words.append(word_obj)
                        global_words.append(word_obj)

                seg.words = segment_valid_words
                seg.text = " ".join([w["word"] for w in segment_valid_words])

            db.commit()

            percent = int(((j + len(batch)) / len(all_segments)) * 100)
            # Отправляем инфо в RabbitMQ/Redis для фронта
            self.update_state(state='PROGRESS', meta={'stage': 'transcribing', 'percent': percent})

        # 4. ФИНАЛЬНАЯ СБОРКА
        global_words.sort(key=lambda x: x['start'])
        media.full_text = " ".join([w['word'] for w in global_words])
        media.status = MediaStatus.COMPLETED
        db.commit()

        return {"status": "SUCCESS"}

    except Exception as e:
        db.rollback()
        if 'media' in locals() and media:
            media.status = MediaStatus.FAILED
            db.commit()
        raise e
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
