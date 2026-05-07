"""
Video 插件 API
"""

import logging
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file, abort

from app.config import Config
from app.models import Media, MediaType
from .service import VideoService

logger = logging.getLogger(__name__)

video_bp = Blueprint("video", __name__)
THUMBNAIL_DIR = Path("/home/ygd/data/thumbnails")


# ────────────────────────────────────────────────────────────
#  扫描
# ────────────────────────────────────────────────────────────
@video_bp.get("/scan/status")
def scan_status():
    from app.scheduler.tasks.video_scan import get_scan_state
    return _ok(get_scan_state())


@video_bp.post("/scan/trigger")
def scan_trigger():
    from app.scheduler.tasks.video_scan import video_scan, get_scan_state
    if get_scan_state()["is_running"]:
        return _err(409, "扫描正在进行中")
    from app.extensions import scheduler
    scheduler.add_job(video_scan, id="video_scan_manual", replace_existing=True)
    return _ok({"message": "视频扫描任务已提交"})


# ────────────────────────────────────────────────────────────
#  列表 + 详情
# ────────────────────────────────────────────────────────────
@video_bp.get("")
def video_list():
    result = VideoService.list_media(
        page=request.args.get("page", 1, type=int),
        per_page=request.args.get("per_page", 20, type=int),
        keyword=request.args.get("keyword", "").strip(),
        parent_id=request.args.get("parent_id", type=int),
    )
    return _ok(result)


@video_bp.get("/<int:video_id>")
def video_detail(video_id: int):
    detail = VideoService.get_detail(video_id)
    if not detail:
        return _err(404, "视频不存在")
    return _ok(detail)


# ────────────────────────────────────────────────────────────
#  ⭐ 视频流播放（支持拖动进度条，HTTP 206 Range 请求）
# ────────────────────────────────────────────────────────────
@video_bp.get("/<int:video_id>/stream")
def video_stream(video_id: int):
    media = Media.query.get(video_id)
    if not media or media.media_type != MediaType.VIDEO:
        abort(404)

    abs_path = Path(Config.VIDEO_BASE_PATH) / media.relative_path
    if not abs_path.is_file():
        abort(404)

    # conditional=True 自动处理 HTTP Range 请求，实现视频拖拽
    return send_file(str(abs_path), conditional=True)


# ────────────────────────────────────────────────────────────
#  删除
# ────────────────────────────────────────────────────────────
@video_bp.delete("/<int:video_id>")
def delete_video(video_id: int):
    if not VideoService.delete(video_id):
        return _err(404, "视频不存在")
    from app.extensions import db
    db.session.commit()
    return _ok({"message": "已删除"})


@video_bp.get('/thumbnail/<file_hash>')
def get_video_thumbnail(file_hash):
    """根据文件的 hash 直接返回对应的缩略图"""
    # 防止恶意遍历路径，只保留合法文件名字符
    safe_hash = Path(file_hash).stem
    thumb_file = f"{safe_hash}.jpg"
    thumb_abs = THUMBNAIL_DIR / thumb_file

    if not thumb_abs.exists():
        return {"msg": "暂无缩略图"}, 404

    return send_from_directory(str(THUMBNAIL_DIR), thumb_file, mimetype='image/jpeg')

# ────────────────────────────────────────────────────────────
#  工具
# ────────────────────────────────────────────────────────────
def _ok(data=None, msg="ok"):
    return jsonify({"code": 0, "msg": msg, "data": data})

def _err(code=400, msg="error"):
    return jsonify({"code": code, "msg": msg, "data": None}), code
