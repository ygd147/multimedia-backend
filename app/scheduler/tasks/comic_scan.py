"""
漫画扫描定时任务
═══════════════════
此文件独立于 comic 插件，是调度器层面的任务封装。
负责：
  1. 创建 app_context（后台线程无 flask 上下文）
  2. 调用 ComicScanner 执行扫描
  3. 维护扫描状态（供 API 查询）
  4. 异常处理与日志

添加新的定时任务只需在此目录下新建 .py 文件，
然后在对应插件的 get_tasks() 中引用即可。
"""

import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# 扫描状态（线程安全）
# ────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "is_running": False,
    "last_run": None,
    "last_stats": None,
    "error": None,
}


def get_scan_state() -> dict:
    """获取当前扫描状态（供 API 调用）"""
    with _lock:
        return {
            "is_running": _state["is_running"],
            "last_run": _state["last_run"],
            "last_stats": _state["last_stats"],
            "error": _state["error"],
        }


def comic_scan():
    """
    漫画扫描任务入口 — 由 APScheduler 调用。
    在后台线程中运行，需要手动创建 app_context。
    """
    with _lock:
        if _state["is_running"]:
            logger.warning("扫描正在进行中，跳过本次执行")
            return
        _state["is_running"] = True
        _state["error"] = None

    logger.info("📡 [定时任务] 开始执行漫画扫描")

    try:
        app = _get_app()
        if app is None:
            raise RuntimeError("App 引用丢失，无法执行扫描")

        with app.app_context():
            from app.config import Config
            from app.plugins.comic.scanner import ComicScanner

            scanner = ComicScanner(Config.COMIC_BASE_PATH)
            stats = scanner.scan()

        with _lock:
            _state["last_run"] = datetime.now().isoformat()
            _state["last_stats"] = stats.to_dict()

        logger.info("📡 [定时任务] 漫画扫描完成: %s", stats.to_dict())

    except Exception as e:
        logger.error("📡 [定时任务] 漫画扫描失败: %s", e, exc_info=True)
        with _lock:
            _state["error"] = str(e)
    finally:
        with _lock:
            _state["is_running"] = False


def _get_app():
    """获取 Flask app 引用（供后台线程使用）"""
    from app.extensions import get_app
    return get_app()
