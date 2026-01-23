# app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from modules.auth.router import router as auth_router
from modules.auth.config import auth_config
from modules.shared.database import init_db, create_tables


app = FastAPI(title="Video Processing Platform", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=auth_config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем роутер модуля auth
app.include_router(auth_router)


# События при старте
@asynccontextmanager
async def lifespan():
    await init_db()
    await create_tables()  # Создаем таблицы, если их нет
    print("Auth module started")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
