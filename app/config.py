"""配置管理 — 从环境变量读取"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # ---------- 数据库 ----------
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "comic_db")

    @staticmethod
    def SQLALCHEMY_DATABASE_URI():
        return (
            f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}"
            f"@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}"
            f"?charset=utf8mb4"
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
    }

    # ---------- 漫画 ----------
    COMIC_BASE_PATH = os.getenv("COMIC_BASE_PATH", "/data/comics")

    # ---------- 插件 ----------
    ENABLED_PLUGINS = [
        p.strip() for p in os.getenv("ENABLED_PLUGINS", "comic").split(",") if p.strip()
    ]

    # ---------- 定时任务 ----------
    SCAN_INTERVAL_HOURS = int(os.getenv("SCAN_INTERVAL_HOURS", 6))
