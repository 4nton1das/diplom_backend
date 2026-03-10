import time
import gc
import torch
import soundfile as sf
import nemo.collections.asr as nemo_asr
from pathlib import Path
from modules.asr.config import asr_config

_model = None


def load_model():
    global _model
    if _model is None:
        print("Загрузка модели Parakeet...")
        start = time.time()
        _model = nemo_asr.models.ASRModel.from_pretrained(model_name=asr_config.model_name)
        if asr_config.device == "cuda" and torch.cuda.is_available():
            _model = _model.cuda()
        print(f"Модель загружена за {time.time()-start:.2f} сек")
    return _model


def split_audio(audio, sr, segment_length=30, overlap=2.0):
    """
    Разбивает аудио (numpy array) на сегменты с перекрытием.
    Возвращает список (start_sample, end_sample).
    """
    segment_samples = int(segment_length * sr)
    overlap_samples = int(overlap * sr)
    step = segment_samples - overlap_samples

    segments = []
    for start in range(0, len(audio), step):
        end = min(start + segment_samples, len(audio))
        if end - start < sr:  # если осталось меньше 1 секунды, пропускаем
            break
        segments.append((start, end))
    return segments


def transcribe_segments(audio, sr, segments, temp_dir="temp_asr"):
    """
    Транскрибирует список сегментов (как numpy массивы) батчами.
    segments: список кортежей (start, end)
    Возвращает список сегментов с абсолютным временем и текстом.
    """
    model = load_model()
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(exist_ok=True)

    all_segments = []
    batch_size = asr_config.batch_size

    # Сохраняем каждый сегмент во временный файл (модель ожидает файлы)
    temp_files = []
    for i, (start, end) in enumerate(segments):
        seg_audio = audio[start:end]
        temp_path = temp_dir / f"seg_{i:04d}.wav"
        sf.write(temp_path, seg_audio, sr)
        temp_files.append((temp_path, start / sr))  # абсолютное время начала

    # Обрабатываем батчами
    for i in range(0, len(temp_files), batch_size):
        batch = temp_files[i:i+batch_size]
        batch_paths = [str(p[0]) for p in batch]

        with torch.no_grad():
            outputs = model.transcribe(batch_paths, timestamps=True)

        for (temp_path, abs_start), hyp in zip(batch, outputs):
            if hasattr(hyp, 'timestamp') and 'segment' in hyp.timestamp:
                for seg in hyp.timestamp['segment']:
                    all_segments.append({
                        'start': abs_start + seg['start'],
                        'end': abs_start + seg['end'],
                        'text': seg['segment']
                    })
            else:
                all_segments.append({
                    'start': abs_start,
                    'end': abs_start + (end - start) / sr,
                    'text': hyp.text
                })

        # Удаляем временные файлы после обработки батча
        for temp_path, _ in batch:
            temp_path.unlink(missing_ok=True)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

    return all_segments
