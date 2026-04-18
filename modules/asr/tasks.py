# modules/asr/tasks.py
import os
import shutil
import tempfile
import bentoml
from modules.shared.celery import celery_app
from pydub import AudioSegment
from pydub.utils import make_chunks
from modules.media.storage import s3_storage  # Теперь этот импорт заработает

# Клиент для связи с микросервисом
asr_client = bentoml.SyncHTTPClient("http://localhost:3000")


@celery_app.task(bind=True, name="asr.process_media")
def process_media_task(self, media_id: int, s3_path: str):
    # tmp_dir — это временная папка на диске твоего ПК (например в AppData/Local/Temp).
    # Она создается автоматически функцией mkdtemp().
    tmp_dir = tempfile.mkdtemp()

    # local_file — это путь к файлу внутри этой временной папки.
    local_file = os.path.join(tmp_dir, "source_media.tmp")

    try:
        # 1. ЗАГРУЗКА ИЗ MINIO
        s3_storage.download_file(s3_path, local_file)

        self.update_state(state='PROGRESS', meta={'status': 'Downloaded'})

        # 2. НАРЕЗКА
        audio = AudioSegment.from_file(local_file).set_channels(1).set_frame_rate(16000)
        chunks = make_chunks(audio, 30000)  # по 30 секунд

        chunk_paths = []
        for i, chunk in enumerate(chunks):
            cp = os.path.join(tmp_dir, f"chunk_{i}.wav")
            chunk.export(cp, format="wav")
            chunk_paths.append(cp)

        # 3. ИНФЕРЕНС ПАЧКАМИ
        final_text_parts = []
        total = len(chunk_paths)

        for i in range(0, total, 8):
            batch = chunk_paths[i:i + 8]

            # ИСПРАВЛЕНО: В BentoML SyncHTTPClient метод вызывается через .call()
            batch_res = asr_client.call("transcribe", paths=batch)

            for res in batch_res:
                final_text_parts.append(res["text"])

            percent = int(((i + len(batch)) / total) * 100)
            self.update_state(state='PROGRESS', meta={'percent': percent})
            print(f"Media {media_id}: {percent}% готово")

        # 4. ФИНАЛИЗАЦИЯ
        full_transcription = " ".join(final_text_parts)

        # Печатаем результат, чтобы IDE не ругалась на неиспользуемую переменную
        print(f"Транскрипция завершена. Длина: {len(full_transcription)} симв.")

        # В ДАЛЬНЕЙШЕМ: здесь будет вызов update_db(media_id, full_transcription)
        return {"status": "SUCCESS", "text_snippet": full_transcription[:100]}

    except Exception as e:
        print(f"ОШИБКА ВОРКЕРА: {e}")
        raise e
    finally:
        # Удаляем временную папку и все чанки внутри неё
        shutil.rmtree(tmp_dir, ignore_errors=True)
