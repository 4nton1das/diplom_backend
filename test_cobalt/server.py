import os
import httpx
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class VideoRequest(BaseModel):
    url: str


# ЭТОТ ЭНДПОИНТ ДОЛЖЕН НАЗЫВАТЬСЯ ТАК ЖЕ, КАК ВО Vue
@app.post("/process-video")
async def process_video(request: VideoRequest):
    # Адрес твоего запущенного Docker-контейнера
    COBALT_LOCAL = "http://localhost:9000/"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Спрашиваем у своего Cobalt ссылку на аудио
            response = await client.post(
                COBALT_LOCAL,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "url": request.url,
                    "downloadMode": "audio",
                    "audioFormat": "mp3"
                }
            )
            return response.json()
        except Exception as e:
            return {"status": "error", "text": f"Docker Cobalt не отвечает: {str(e)}"}


@app.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            buffer.write(chunk)
    return {"status": "success", "filename": file.filename}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
