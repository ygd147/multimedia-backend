# app/extensions.py
"""
全局扩展单例
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

db = SQLAlchemy()

scheduler = BackgroundScheduler(
    timezone="Asia/Shanghai",
    job_defaults={
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 3600,
    },
)

_app_ref: Flask | None = None


def store_app(app: Flask):
    global _app_ref
    _app_ref = app


def get_app() -> Flask | None:
    return _app_ref


def shutdown_scheduler():
    """停止调度器 — 放在这里统一管理"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
