"""
Flask App 工厂
═══════════════════════════════════════════════════════
启动顺序：
  1. 加载配置
  2. 初始化扩展 (SQLAlchemy / CORS)
  3. 注册所有 ORM 模型（确保 create_all 能建表）
  4. 发现并注册插件
  5. 初始化调度器（含启动扫描）
"""

import logging
import atexit

from flask import Flask, jsonify
from flask_cors import CORS

from app.config import Config
from app.extensions import db, scheduler, shutdown_scheduler
from app.plugins import PluginManager

# 确保所有模型被导入，否则 create_all 不会建表
import app.models  # noqa: F401

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)

    # ── 1. 配置 ──
    app.config.from_object(Config)
    app.config["SQLALCHEMY_DATABASE_URI"] = Config.SQLALCHEMY_DATABASE_URI()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = Config.SQLALCHEMY_ENGINE_OPTIONS

    # ── 2. 日志 ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── 3. 扩展 ──
    db.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # ── 4. 建表 ──
    with app.app_context():
        db.create_all()
        logger.info("📦 数据库表已就绪")

    # ── 5. 插件 ──
    PluginManager.discover(Config.ENABLED_PLUGINS)
    PluginManager.register_blueprints(app)

    # ── 6. 调度器 ──
    from app.scheduler import init_scheduler
    init_scheduler(app)

    # ── 7. 退出清理 ──
    atexit.register(_on_exit, app)

    # ── 8. 健康检查 ──
    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "comic-backend"})

    # ── 9. API 根（列出已注册插件）─
    @app.get("/api")
    def api_index():
        plugins = list(PluginManager.all_plugins().keys())
        return jsonify({
            "code": 0,
            "msg": "ok",
            "data": {"plugins": plugins, "endpoints": [f"/api/{p}" for p in plugins]},
        })

    return app


def _on_exit(app):
    """进程退出时清理"""
    logger.info("🔄 应用关闭中...")
    PluginManager.fire_on_shutdown(app)
    shutdown_scheduler()
    logger.info("👋 应用已关闭")
