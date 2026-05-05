"""Video 业务逻辑层"""

import logging
from pathlib import Path
from typing import Optional

from app.extensions import db
from app.config import Config
from app.models import Media, MediaType

logger = logging.getLogger(__name__)


class VideoService:

    @staticmethod
    def list_media(
        page: int = 1,
        per_page: int = 20,
        keyword: str = "",
        parent_id: Optional[int] = None,
    ) -> dict:
        q = Media.query

        # 自动解析 video 根目录
        if parent_id is None:
            root = Media.query.filter(
                Media.file_name == "video",
                Media.parent_id.is_(None),
            ).first()
            if root:
                parent_id = root.id

        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.media_type == MediaType.VIDEO)

        if keyword:
            q = q.filter(Media.file_name.ilike(f"%{keyword}%"))

        total = q.count()
        items = q.order_by(Media.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
        return {"items": [_serialize(m) for m in items], "total": total, "page": page, "per_page": per_page}

    @staticmethod
    def get_detail(video_id: int) -> Optional[dict]:
        media = Media.query.get(video_id)
        if not media or media.media_type not in (MediaType.VIDEO, MediaType.DIRECTORY):
            return None
        result = _serialize(media)
        result["children_count"] = Media.query.filter(Media.parent_id == video_id).count()
        return result

    @staticmethod
    def delete(video_id: int) -> bool:
        media = Media.query.get(video_id)
        if not media:
            return False
        for child in Media.query.filter(Media.parent_id == video_id).all():
            VideoService.delete(child.id)
        db.session.delete(media)
        return True


def _serialize(m: Media) -> dict:
    category = None
    if m.is_dir and m.media_type == MediaType.DIRECTORY:
        category = "directory"
    elif not m.is_dir and m.media_type == MediaType.VIDEO:
        category = "video"

    return {
        "id": m.id, "file_hash": m.file_hash, "media_type": m.media_type,
        "file_name": m.file_name, "relative_path": m.relative_path,
        "file_size": m.file_size, "status": m.status, "parent_id": m.parent_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "is_dir": m.is_dir, "dir_name": m.dir_name, "category": category,
    }
