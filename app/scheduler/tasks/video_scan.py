"""视频扫描任务（纯逻辑）"""

import logging
from app.config import Config

logger = logging.getLogger(__name__)

_state = {"is_running": False}

def get_scan_state() -> dict:
    return {"is_running": _state["is_running"]}

def video_scan():
    if _state["is_running"]:
        logger.warning("🎬 [视频扫描] 正在进行中，跳过")
        return
    _state["is_running"] = True
    try:
        from app import create_app
        app = create_app()
        with app.app_context():
            from app.plugins.video.scanner import VideoScanner
            scanner = VideoScanner(Config.VIDEO_BASE_PATH)
            scanner.scan()
    except Exception as e:
        logger.error("🎬 [视频扫描] 失败: %s", e, exc_info=True)
    finally:
        _state["is_running"] = False
