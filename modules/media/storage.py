# modules/media/storage.py
import boto3
from botocore.client import Config


class S3Storage:
    def __init__(self):
        self.bucket_name = "test-audio"
        self.s3 = boto3.client(
            's3',
            endpoint_url="http://localhost:9002",
            aws_access_key_id="admin",
            aws_secret_access_key="supersecretpassword",
            config=Config(signature_version='s3v4'),
            region_name='us-east-1'
        )

    def download_file(self, s3_path, local_path):
        """Скачивает файл из MinIO на жесткий диск воркера"""
        self.s3.download_file(self.bucket_name, s3_path, local_path)


# Создаем экземпляр для импорта
s3_storage = S3Storage()
