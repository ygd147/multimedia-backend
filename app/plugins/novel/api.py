"""
Novel 插件 API
"""

import logging
import threading

from flask import Blueprint, request, jsonify, current_app

from .service import NovelService

logger = logging.getLogger(__name__)

novel_bp = Blueprint("novel", __name__)

# ⭐ 本地并发锁，代替 switcher，防止重复触发扫描
_is_scanning = False


# ────────────────────────────────────────────────────────────
#  扫描控制
# ────────────────────────────────────────────────────────────
@novel_bp.get("/scan/status")
def scan_status():
    return _ok({"is_running": _is_scanning})


@novel_bp.post("/scan/trigger")
def scan_trigger():
    global _is_scanning
    if _is_scanning:
        return _err(409, "扫描正在进行中")

    real_app = current_app._get_current_object()

    def _task():
        global _is_scanning
        with real_app.app_context():
            from .scanner import NovelScanner
            base_path = real_app.config.get("NOVEL_BASE_PATH")
            _is_scanning = True
            try:
                NovelScanner(base_path).scan()
            except Exception as e:
                logger.error("手动小说扫描失败: %s", e, exc_info=True)
            finally:
                _is_scanning = False

    threading.Thread(target=_task, daemon=True).start()
    return _ok({"message": "小说扫描任务已提交"})


@novel_bp.post("/scan/reset")
def scan_reset():
    global _is_scanning
    _is_scanning = False
    return _ok({"message": "扫描状态已重置"})


# ────────────────────────────────────────────────────────────
#  列表
# ────────────────────────────────────────────────────────────
@novel_bp.get("")
def novel_list():
    result = NovelService.list_media(
        page=request.args.get("page", 1, type=int),
        per_page=request.args.get("per_page", 20, type=int),
        keyword=request.args.get("keyword", "").strip(),
        parent_id=request.args.get("parent_id", type=int),
    )
    return _ok(result)


# ────────────────────────────────────────────────────────────
#  章节目录 + 章节正文
# ────────────────────────────────────────────────────────────
@novel_bp.get("/<int:novel_id>/chapters")
def novel_chapters(novel_id: int):
    chapters = NovelService.get_chapters(novel_id)
    if not chapters:
        return _err(404, "小说不存在或无章节")
    return _ok(chapters)


@novel_bp.get("/chapter/<int:chapter_id>")
def novel_chapter_detail(chapter_id: int):
    content = NovelService.get_chapter_content(chapter_id)
    if not content:
        return _err(404, "章节不存在")
    return _ok(content)


# ────────────────────────────────────────────────────────────
#  删除 (代码级联清理)
# ────────────────────────────────────────────────────────────
@novel_bp.delete("/<int:novel_id>")
def delete_novel(novel_id: int):
    if not NovelService.delete(novel_id):
        return _err(404, "小说不存在或已被删除")
    from app.extensions import db
    db.session.commit()
    return _ok({"message": "小说及章节已彻底删除"})


# ────────────────────────────────────────────────────────────
#  工具
# ────────────────────────────────────────────────────────────
def _ok(data=None, msg="ok"):
    return jsonify({"code": 0, "msg": msg, "data": data})

def _err(code=400, msg="error"):
    return jsonify({"code": code, "msg": msg, "data": None}), code
