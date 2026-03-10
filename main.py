# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from modules.auth.router import router as auth_router
from modules.media.router import router as media_router
from modules.auth.config import auth_config
from modules.shared.database import init_db, create_tables


app = FastAPI(title="VideoSummarizer")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=auth_config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роутеры
app.include_router(auth_router)
app.include_router(media_router)


# События при старте
@app.on_event("startup")
async def startup_event():
    await init_db()
    await create_tables()  # Создаем таблицы, если их нет

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
