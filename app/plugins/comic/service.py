"""Comic 业务逻辑层（按需解压版）"""

import logging
import zipfile
from pathlib import Path
from typing import Optional

from app.extensions import db
from app.config import Config
from app.models import Media, MediaImageMeta, MediaType

logger = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".avif",
})


class ComicService:

    @staticmethod
    def list_media(
        page: int = 1,
        per_page: int = 20,
        media_type: Optional[int] = None,
        keyword: str = "",
        parent_id: Optional[int] = None,
    ) -> dict:
        # --- 核心逻辑分支：由 parent_id 决定一切 ---
        
        # 分支 A：明确传入了 parent_id -> 【浏览模式】
        if parent_id is not None:
            q = Media.query.filter(Media.parent_id == parent_id)
            if media_type is not None:
                q = q.filter(Media.media_type == media_type)
            if keyword:
                q = q.filter(Media.file_name.ilike(f"%{keyword}%"))
            
            # 排序：目录永远在前，内部按时间倒序
            items = q.order_by(
                Media.is_dir.desc(), 
                Media.created_at.desc()
            ).offset((page - 1) * per_page).limit(per_page).all()
            
            total = q.count()

        # 分支 B：未传入 parent_id -> 【聚合搜索模式】
        else:
            # 基础查询构建
            base_q = Media.query
            if media_type is not None:
                base_q = base_q.filter(Media.media_type == media_type)
            if keyword:
                base_q = base_q.filter(Media.file_name.ilike(f"%{keyword}%"))

            # 1. 先获取所有符合条件的记录的 ID 关系
            all_candidates = base_q.with_entities(Media.id, Media.parent_id).all()
            
            if not all_candidates:
                return {"items": [], "total": 0, "page": page, "per_page": per_page}

            # 2. 计算聚合后的展示 ID
            display_ids = set()
            for mid, mpid in all_candidates:
                # 逻辑：有爸爸找爸爸，没爸爸展示自己
                display_ids.add(mpid if mpid is not None else mid)
            
            # 3. 查询最终要展示的对象
            final_q = Media.query.filter(Media.id.in_(display_ids))
            
            # 排序：目录排前
            final_query = final_q.order_by(
                Media.is_dir.desc(),
                Media.created_at.desc()
            )
            
            # 分页
            total = final_query.count()
            items = final_query.offset((page - 1) * per_page).limit(per_page).all()

        return {
            "items": [_serialize(m) for m in items], 
            "total": total, 
            "page": page, 
            "per_page": per_page
        }
    @staticmethod
    def get_detail(media_id: int) -> Optional[dict]:
        media = Media.query.get(media_id)
        if not media:
            return None
        result = _serialize(media)
        result["meta"] = _get_meta(media_id, media.is_dir)
        result["children_count"] = Media.query.filter(Media.parent_id == media_id).count()
        result["pages"] = ComicService.get_pages(media_id)
        return result

    # ============================================================
    #  页面列表
    # ============================================================
    @staticmethod
    def get_pages(media_id: int) -> Optional[list]:
        media = Media.query.get(media_id)
        if not media or media.media_type != MediaType.IMAGE:
            return None

        if media.is_dir == 1:
            abs_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not abs_path.is_dir():
                return None
            try:
                images = sorted(
                    (f for f in abs_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                    key=lambda f: f.name.lower(),
                )
                return [{"index": i, "file_name": f.name, "type": "folder"} for i, f in enumerate(images)]
            except OSError:
                return None
        else:
            # ⭐ 实时遍历 ZIP 目录获取列表（毫秒级，无需存数据库）
            zip_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not zip_path.exists():
                return None
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    entries = [
                        info for info in zf.infolist()
                        if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_EXTS
                    ]
                    entries.sort(key=lambda e: Path(e.filename).name.lower())
                    return [{"index": i, "file_name": Path(e.filename).name, "file_path": e.filename, "type": "zip"} for i, e in enumerate(entries)]
            except (zipfile.BadZipFile, OSError) as e:
                logger.error("读取ZIP目录失败 media=%d: %s", media_id, e)
                return None

    # ============================================================
    #  读取指定页
    # ============================================================
    @staticmethod
    def read_page(media_id: int, page_index: int):
        media = Media.query.get(media_id)
        if not media or media.media_type != MediaType.IMAGE:
            return None

        if media.is_dir == 1:
            abs_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not abs_path.is_dir():
                return None
            try:
                images = sorted(
                    (f for f in abs_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                    key=lambda f: f.name.lower(),
                )
                if page_index < 0 or page_index >= len(images):
                    return None
                return images[page_index].read_bytes(), _mime(images[page_index].suffix)
            except (OSError, IndexError):
                return None
        else:
            zip_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not zip_path.exists():
                return None
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    entries = [
                        info for info in zf.infolist()
                        if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_EXTS
                    ]
                    entries.sort(key=lambda e: Path(e.filename).name.lower())
                    if page_index < 0 or page_index >= len(entries):
                        return None
                    data = zf.read(entries[page_index].filename)
                    return data, _mime(Path(entries[page_index].filename).suffix)
            except (zipfile.BadZipFile, KeyError, OSError) as e:
                logger.error("解压ZIP图片失败 media=%d page=%d: %s", media_id, page_index, e)
                return None

    # ============================================================
    #  删除
    # ============================================================
    @staticmethod
    def delete(comic_id: int) -> bool:
        media = Media.query.get(comic_id)
        if not media:
            return False
        for child in Media.query.filter(Media.parent_id == comic_id).all():
            ComicService.delete(child.id)
        if media.media_type == MediaType.IMAGE:
            MediaImageMeta.query.filter_by(media_id=comic_id).delete()
        db.session.delete(media)
        return True


def _serialize(m: Media) -> dict:
    category = None
    if m.is_dir and m.media_type == MediaType.DIRECTORY:
        category = "directory"
    elif m.is_dir and m.media_type == MediaType.IMAGE:
        category = "image_folder"
    elif not m.is_dir and m.media_type == MediaType.IMAGE:
        category = "archive"
    return {
        "id": m.id, "file_hash": m.file_hash, "media_type": m.media_type,
        "file_name": m.file_name, "relative_path": m.relative_path,
        "file_size": m.file_size, "status": m.status, "parent_id": m.parent_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "is_dir": m.is_dir, "dir_name": m.dir_name, "category": category,
    }


def _get_meta(media_id: int, is_dir: int) -> Optional[dict]:
    media = Media.query.get(media_id)
    if media and media.media_type == MediaType.DIRECTORY:
        return None
    meta = MediaImageMeta.query.filter_by(media_id=media_id).first()
    if not meta:
        return None
    return {
        "width": meta.width, "height": meta.height, "is_archive": meta.is_archive,
        "page_count": meta.page_count, "main_color": meta.main_color,
    }


def _mime(suffix: str) -> str:
    s = suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".avif": "image/avif", ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(s, "application/octet-stream")
