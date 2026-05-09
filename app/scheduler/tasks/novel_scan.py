"""小说扫描任务（纯逻辑）"""

import logging
from app.config import Config

logger = logging.getLogger(__name__)

_state = {"is_running": False}

def get_scan_state() -> dict:
    return {"is_running": _state["is_running"]}

def novel_scan():
    if _state["is_running"]:
        logger.warning("📖 [小说扫描] 正在进行中，跳过")
        return
    
    _state["is_running"] = True
    try:
        # 在后台线程或定时任务中，需要独立创建 app 上下文
        from app import create_app
        app = create_app()
        with app.app_context():
            from app.plugins.novel.scanner import NovelScanner
            scanner = NovelScanner(Config.NOVEL_BASE_PATH)
            scanner.scan()
    except Exception as e:
        logger.error("📖 [小说扫描] 失败: %s", e, exc_info=True)
    finally:
        _state["is_running"] = False
