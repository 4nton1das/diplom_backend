from pydantic_settings import BaseSettings
import torch


class ASRConfig(BaseSettings):
    model_name: str = "nvidia/parakeet-tdt-0.6b-v3"
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    batch_size: int = 4
    sample_rate: int = 16000
    segment_length: int = 30
    overlap_seconds: float = 2.0
    temp_dir: str = "temp_asr"


asr_config = ASRConfig()
