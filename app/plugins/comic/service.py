"""Comic 业务逻辑层"""

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from app.config import Config
from app.models import Media, MediaImageMeta, MediaZipChild, MediaType

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
        q = Media.query

        # ⭐ 核心：指定了 media_type 但没传 parent_id → 自动找到根目录
        if media_type is not None and parent_id is None:
            type_root_map = {1: "comic", 2: "novel", 3: "video"}
            root_name = type_root_map.get(media_type)
            if root_name:
                root = Media.query.filter(
                    Media.file_name == root_name,
                    Media.parent_id.is_(None),
                ).first()
                if root:
                    parent_id = root.id

        # 传了 parent_id → 只看直接子级（目录+文件都要显示，方便导航）
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        elif media_type is not None:
            q = q.filter(Media.media_type == media_type)

        if keyword:
            q = q.filter(Media.file_name.ilike(f"%{keyword}%"))

        total = q.count()
        items = q.order_by(Media.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        return {"items": [_serialize(m) for m in items], "total": total, "page": page, "per_page": per_page}

    @staticmethod
    def get_detail(media_id: int) -> Optional[dict]:
        media = Media.query.get(media_id)
        if not media:
            return None
        result = _serialize(media)
        result["meta"] = _get_meta(media_id, media.is_dir)
        result["children_count"] = Media.query.filter(Media.parent_id == media_id).count()
        # ⭐ 附带页面列表
        result["pages"] = ComicService.get_pages(media_id)
        return result

    # ============================================================
    #  ⭐ 页面列表 — ZIP 和图片文件夹通用
    # ============================================================
    @staticmethod
    def get_pages(media_id: int) -> Optional[list]:
        media = Media.query.get(media_id)
        if not media or media.media_type != MediaType.IMAGE:
            return None
        if media.media_type == MediaType.DIRECTORY:
            return None

        if media.is_dir == 1:
            # 图片文件夹 → 扫描磁盘
            abs_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not abs_path.is_dir():
                return None
            try:
                images = sorted(
                    (f for f in abs_path.iterdir()
                     if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                    key=lambda f: f.name.lower(),
                )
                return [{"index": i, "file_name": f.name, "type": "folder"} for i, f in enumerate(images)]
            except OSError:
                return None

        else:
            # ZIP → 从 media_zip_child 读
            children = (
                MediaZipChild.query
                .filter_by(media_id=media_id)
                .order_by(MediaZipChild.sort_order)
                .all()
            )
            return [
                {"index": i, "file_name": c.file_name, "file_path": c.file_path, "type": "zip"}
                for i, c in enumerate(children)
            ]

    # ============================================================
    #  ⭐ 读取指定页的图片数据
    # ============================================================
    @staticmethod
    def read_page(media_id: int, page_index: int):
        """返回 (bytes, mimetype) 或 None"""
        media = Media.query.get(media_id)
        if not media or media.media_type != MediaType.IMAGE:
            return None

        if media.is_dir == 1:
            # 图片文件夹
            abs_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not abs_path.is_dir():
                return None
            try:
                images = sorted(
                    (f for f in abs_path.iterdir()
                     if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                    key=lambda f: f.name.lower(),
                )
                if page_index < 0 or page_index >= len(images):
                    return None
                return images[page_index].read_bytes(), _mime(images[page_index].suffix)
            except (OSError, IndexError):
                return None

        else:
            # ZIP → 实时解压单张图
            import zipfile
            children = (
                MediaZipChild.query
                .filter_by(media_id=media_id)
                .order_by(MediaZipChild.sort_order)
                .all()
            )
            if page_index < 0 or page_index >= len(children):
                return None

            zip_path = Path(Config.COMIC_BASE_PATH) / media.relative_path
            if not zip_path.exists():
                return None

            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    data = zf.read(children[page_index].file_path)
                return data, _mime(Path(children[page_index].file_name).suffix)
            except (zipfile.BadZipFile, KeyError, OSError) as e:
                logger.error("读取ZIP图片失败 media=%d page=%d: %s", media_id, page_index, e)
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
            MediaZipChild.query.filter_by(media_id=comic_id).delete()
        db.session.delete(media)
        return True


# ════════════════════════════════════════════════════════════
#  内部辅助
# ════════════════════════════════════════════════════════════
def _serialize(m: Media) -> dict:
    category = None
    if m.is_dir and m.media_type == MediaType.DIRECTORY:
        category = "directory"
    elif m.is_dir and m.media_type == MediaType.IMAGE:
        category = "image_folder"
    elif not m.is_dir and m.media_type == MediaType.IMAGE:
        category = "archive"

    return {
        "id": m.id,
        "file_hash": m.file_hash,
        "media_type": m.media_type,
        "file_name": m.file_name,
        "relative_path": m.relative_path,
        "file_size": m.file_size,
        "status": m.status,
        "parent_id": m.parent_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "is_dir": m.is_dir,
        "dir_name": m.dir_name,
        "category": category,
    }


def _get_meta(media_id: int, is_dir: int) -> Optional[dict]:
    media = Media.query.get(media_id)
    if media and media.media_type == MediaType.DIRECTORY:
        return None
    img_meta = MediaImageMeta.query.filter_by(media_id=media_id).first()
    if not img_meta:
        return None

    if img_meta.is_archive == 1:
        children = MediaZipChild.query.filter_by(media_id=media_id).order_by(MediaZipChild.sort_order).all()
        return {
            "thumb_path": img_meta.thumb_path,
            "is_archive": 1,
            "children": [{"id": c.id, "file_name": c.file_name, "thumb_path": c.thumb_path, "file_path": c.file_path, "width": c.width, "height": c.height} for c in children],
        }
    else:
        return {
            "width": img_meta.width, "height": img_meta.height,
            "thumb_path": img_meta.thumb_path, "is_archive": 0, "main_color": img_meta.main_color,
        }


def _mime(suffix: str) -> str:
    s = suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".avif": "image/avif", ".tiff": "image/tiff", ".tif": "image/tiff",
    }.get(s, "application/octet-stream")
