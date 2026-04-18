# modules/asr/service.py
import bentoml
from .config import ASR_SERVICE_URL


class ASRClient:
    def __init__(self):
        self.service_url = ASR_SERVICE_URL

    async def transcribe_s3_file(self, bucket: str, object_name: str):
        # Используем контекстный менеджер для клиента
        async with bentoml.AsyncHTTPClient(self.service_url) as client:
            result = await client.process_audio_s3(
                bucket=bucket,
                object_name=object_name
            )
            return result
