from modules.asr.tasks import process_media_task

result = process_media_task.delay(media_id=1, s3_path="input.mp4")

print(f"Задача отправлена! ID: {result.id}")
