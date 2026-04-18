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

    def upload_file(self, local_path: str, s3_key: str):
        self.s3.upload_file(local_path, self.bucket_name, s3_key)

    def download_file(self, s3_key: str, local_path: str):
        self.s3.download_file(self.bucket_name, s3_key, local_path)


# Создаем экземпляр для импорта
s3_storage = S3Storage()
