"""
Comic 插件 API — 完全对齐前端 axios 请求
"""

import logging
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file, abort, Response

from app.config import Config
from app.models import Media, MediaType
from .service import ComicService

logger = logging.getLogger(__name__)

media_bp = Blueprint("media", __name__)

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".avif",
})


# ────────────────────────────────────────────────────────────
#  扫描
# ────────────────────────────────────────────────────────────
@media_bp.get("/scan/status")
def scan_status():
    from app.scheduler.tasks.comic_scan import get_scan_state
    return _ok(get_scan_state())


@media_bp.post("/scan/trigger")
def scan_trigger():
    from app.scheduler.tasks.comic_scan import comic_scan, get_scan_state
    if get_scan_state()["is_running"]:
        return _err(409, "扫描正在进行中")
    from app.extensions import scheduler
    scheduler.add_job(comic_scan, id="comic_scan_manual", replace_existing=True)
    return _ok({"message": "扫描任务已提交"})


# ────────────────────────────────────────────────────────────
#  列表 + 详情
# ────────────────────────────────────────────────────────────
@media_bp.get("")
def media_list():
    """
    媒体列表接口
    行为逻辑：
    1. 传 parent_id: 浏览模式 -> 返回该目录下的直接子文件/子目录
    2. 不传 parent_id: 聚合模式 -> 全局搜索，结果按父目录聚合去重返回
    """
    # 获取参数
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    media_type = request.args.get("media_type", type=int)
    keyword = request.args.get("keyword", "").strip()
    parent_id = request.args.get("parent_id", type=int)

    # 调用 Service (不再做自动找根目录的逻辑，完全由参数决定行为)
    result = ComicService.list_media(
        page=page,
        per_page=per_page,
        media_type=media_type,
        keyword=keyword,
        parent_id=parent_id,
    )
    return _ok(result)

@media_bp.get("/<int:media_id>")
def media_detail(media_id: int):
    detail = ComicService.get_detail(media_id)
    if not detail:
        return _err(404, "媒体不存在")
    return _ok(detail)


# ────────────────────────────────────────────────────────────
#  ⭐ 页面列表  GET /api/media/<id>/pages
# ────────────────────────────────────────────────────────────
@media_bp.get("/<int:media_id>/pages")
def media_pages(media_id: int):
    pages = ComicService.get_pages(media_id)
    if pages is None:
        return _err(404, "该媒体没有页面")
    return _ok({"pages": pages, "total": len(pages)})


# ────────────────────────────────────────────────────────────
#  ⭐ 读取指定页  GET /api/media/<id>/chapter/<index>
#     对齐前端 getChapterUrl(id, chapterIndex)
#     ZIP → 实时解压单张图返回
#     图片文件夹 → 直接返回第 index 张图
# ────────────────────────────────────────────────────────────
@media_bp.get("/<int:media_id>/chapter/<int:page_index>")
def media_chapter(media_id: int, page_index: int):
    result = ComicService.read_page(media_id, page_index)
    if result is None:
        abort(404)
    data, mime = result
    return Response(data, mimetype=mime)


# ────────────────────────────────────────────────────────────
#  缩略图 / 封面 / 原图
# ────────────────────────────────────────────────────────────
@media_bp.get("/<int:media_id>/thumbnail")
def media_thumbnail(media_id: int):
    # 第一页就是封面/缩略图
    result = ComicService.read_page(media_id, 0)
    if result is None:
        abort(404)
    data, mime = result
    return Response(data, mimetype=mime)


@media_bp.get("/<int:media_id>/raw")
def media_raw(media_id: int):
    media = Media.query.get(media_id)
    if not media or not media.relative_path:
        abort(404)

    abs_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
    if not abs_path.exists():
        abort(404)

    if abs_path.is_file():
        return send_file(str(abs_path))

    # 文件夹 → 返回第一张图
    result = ComicService.read_page(media_id, 0)
    if result is None:
        abort(404)
    data, mime = result
    return Response(data, mimetype=mime)


@media_bp.get("/<int:media_id>/cover")
def media_cover(media_id: int):
    return media_thumbnail(media_id)


@media_bp.get("/<int:media_id>/stream")
def media_stream(media_id: int):
    abort(501, description="Video streaming not implemented yet")


# ────────────────────────────────────────────────────────────
#  删除
# ────────────────────────────────────────────────────────────
@media_bp.delete("/<int:media_id>")
def delete_media(media_id: int):
    if not ComicService.delete(media_id):
        return _err(404, "媒体不存在")
    from app.extensions import db
    db.session.commit()
    return _ok({"message": "已删除"})


# ────────────────────────────────────────────────────────────
#  响应工具
# ────────────────────────────────────────────────────────────
def _ok(data=None, msg="ok"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def _err(code=400, msg="error"):
    return jsonify({"code": code, "msg": msg, "data": None}), code
