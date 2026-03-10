from celery import Celery
from modules.media.config import media_config

celery_app = Celery(
    "worker",
    broker=media_config.celery_broker_url,
    backend=media_config.celery_result_backend,
    include=[
        "modules.media.tasks",
        "modules.asr.tasks",
        # другие модули
    ]
)

# Опционально: настройки
celery_app.conf.task_serializer = 'json'
celery_app.conf.result_serializer = 'json'
celery_app.conf.accept_content = ['json']
celery_app.conf.result_expires = 3600
