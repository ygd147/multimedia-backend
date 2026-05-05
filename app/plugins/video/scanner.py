"""
视频目录扫描器
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.extensions import db
from app.models import Media, MediaType, MediaStatus

logger = logging.getLogger(__name__)

VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".avi", ".rmvb", ".wmv", ".flv", ".mov", ".webm", ".ts", ".m4v",
})
IGNORED_NAMES = frozenset({
    "__pycache__", ".git", ".svn", ".DS_Store", ".thumbnails",
    "node_modules", ".idea", "@eaDir", "System Volume Information",
})


@dataclass
class ScanStats:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

    def to_dict(self) -> dict:
        return {
            "total": self.total, "inserted": self.inserted,
            "updated": self.updated, "skipped": self.skipped,
            "errors": self.errors, "is_running": False,
        }


def _is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in VIDEO_EXTS

def _should_skip(name: str) -> bool:
    return name.startswith(".") or name in IGNORED_NAMES

def fast_file_fingerprint(path: Path) -> str:
    stat = path.stat()
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(str(int(stat.st_mtime * 1000)).encode())
    try:
        with open(path, "rb") as f:
            h.update(f.read(1024 * 1024))  # 只读 1MB 头部
    except Exception:
        pass
    return h.hexdigest()

def classify(path: Path) -> str:
    if _should_skip(path.name):
        return "skip"
    if path.is_file():
        return "video" if _is_video(path) else "skip"
    if path.is_dir():
        return "directory"
    return "skip"


class VideoScanner:
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).resolve()
        self.stats = ScanStats()

    def scan(self) -> ScanStats:
        if not self.base_path.is_dir():
            raise FileNotFoundError(f"目录不存在: {self.base_path}")
        logger.info("🎬 [视频] 开始扫描: %s", self.base_path)
        start = time.perf_counter()
        self._scan_dir(self.base_path, parent_id=None)
        db.session.commit()
        cost = time.perf_counter() - start
        logger.info("✅ [视频] 扫描完成 耗时=%.2fs 统计: %s", cost, self.stats.to_dict())
        return self.stats

    def _scan_dir(self, directory: Path, parent_id: Optional[int]):
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (0 if e.is_dir() else 1, e.name.lower()))
        except (PermissionError, OSError) as e:
            logger.error("无法读取目录 %s: %s", directory, e)
            return

        rel = directory.relative_to(self.base_path) if directory != self.base_path else "."
        logger.info("📂 [视频] 扫描目录 [%s] (%d 项)", rel, len(entries))

        for entry in entries:
            self.stats.total += 1
            cat = classify(entry)
            try:
                rel_path = str(entry.relative_to(self.base_path))
            except ValueError:
                rel_path = entry.name

            try:
                if cat == "video":
                    self._process_video(entry, parent_id, rel_path)
                elif cat == "directory":
                    dir_id = self._process_directory(entry, parent_id, rel_path)
                    if dir_id is not None:
                        self._scan_dir(entry, parent_id=dir_id)
                else:
                    self.stats.skipped += 1
            except Exception as e:
                logger.error("处理失败 [%s] %s: %s", cat, entry, e, exc_info=True)
                self.stats.errors += 1

            if self.stats.total % 50 == 0:
                db.session.commit()

    def _find_existing(self, name: str, parent_id: Optional[int]):
        q = Media.query.filter(Media.file_name == name)
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.parent_id.is_(None))
        return q.first()

    def _process_video(self, path: Path, parent_id, rel_path: str):
        file_hash = fast_file_fingerprint(path)
        file_size = path.stat().st_size

        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_hash and existing.relative_path == rel_path:
            self.stats.skipped += 1
            return

        media = existing or Media(
            media_type=MediaType.VIDEO, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=0,
        )
        media.file_hash = file_hash
        media.relative_path = rel_path
        media.file_size = file_size
        media.media_type = MediaType.VIDEO
        media.is_dir = 0
        media.status = MediaStatus.READY

        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            logger.info("🎬 新增视频: %s (id=%d)", path.name, media.id)
        else:
            self.stats.updated += 1
            logger.info("🎬 更新视频: %s (id=%d)", path.name, media.id)

    def _process_directory(self, path: Path, parent_id, rel_path: str) -> Optional[int]:
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.is_dir == 1 and existing.relative_path == rel_path:
            self.stats.skipped += 1
            return existing.id

        if existing:
            existing.is_dir = 1
            existing.dir_name = path.name
            existing.relative_path = rel_path
            existing.media_type = MediaType.DIRECTORY
            existing.status = MediaStatus.READY
            self.stats.updated += 1
            return existing.id

        media = Media(
            file_hash="", media_type=MediaType.DIRECTORY, file_name=path.name,
            file_size=0, status=MediaStatus.READY, parent_id=parent_id,
            is_dir=1, dir_name=path.name, relative_path=rel_path,
        )
        db.session.add(media)
        db.session.flush()
        self.stats.inserted += 1
        return media.id
