# app/plugins/video/scanner.py
"""
视频目录扫描器（含缩略图生成 + 孤儿清理）
"""

import hashlib
import logging
import os
import subprocess
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

# ⭐ 缩略图配置
THUMBNAIL_DIR = Path("/home/ygd/data/thumbnails")
THUMBNAIL_WIDTH = 320
THUMBNAIL_HEIGHT = 180


@dataclass
class ScanStats:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    deleted: int = 0              # ⭐ 新增
    thumbnails_generated: int = 0 # ⭐ 新增

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "inserted": self.inserted,
            "updated": self.updated,
            "skipped": self.skipped,
            "deleted": self.deleted,                # ⭐ 新增
            "thumbnails_generated": self.thumbnails_generated,  # ⭐ 新增
            "errors": self.errors,
            "is_running": False,
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
            h.update(f.read(1024 * 1024))
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

# ⭐⭐⭐ 新增：FFmpeg 缩略图生成 ⭐⭐⭐
def generate_thumbnail(video_path: Path, save_path: Path) -> bool:
    """用 FFmpeg 从视频中抽取一帧作为缩略图"""
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 探测视频时长
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ],
            capture_output=True, text=True, timeout=30
        )
        try:
            duration = float(probe.stdout.strip())
        except (ValueError, TypeError):
            duration = 0.0

        # 截取时间点：时长 10% 的位置，至少 1 秒
        seek_time = max(1.0, duration * 0.1) if duration > 0 else 1.0

        # 抽帧 + 缩放 + 居中填黑
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(seek_time),
                "-i", str(video_path),
                "-vframes", "1",
                "-vf", (
                    f"scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}"
                    ":force_original_aspect_ratio=decrease,"
                    f"pad={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
                ),
                "-q:v", "2",
                "-loglevel", "error",
                str(save_path)
            ],
            capture_output=True, text=True, timeout=120
        )

        return save_path.exists() and save_path.stat().st_size > 0

    except FileNotFoundError:
        logger.warning("⚠️ FFmpeg 未安装，跳过缩略图生成")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ FFmpeg 超时: %s", video_path)
        return False
    except Exception as e:
        logger.warning("⚠️ 缩略图生成失败: %s -> %s", video_path, e)
        return False


class VideoScanner:
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).resolve()
        self.stats = ScanStats()
        # ⭐ 新增：记录扫描到的所有路径，用于清理孤儿
        self.scanned_paths: set[str] = set()

    def scan(self) -> ScanStats:
        if not self.base_path.is_dir():
            raise FileNotFoundError(f"目录不存在: {self.base_path}")
        logger.info("🎬 [视频] 开始扫描: %s", self.base_path)
        start = time.perf_counter()

        # ⭐ 确保缩略图目录存在
        THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

        self._scan_dir(self.base_path, parent_id=None)

        # ⭐⭐⭐ 清理孤儿数据 ⭐⭐⭐
        self._cleanup_orphans()

        db.session.commit()
        cost = time.perf_counter() - start
        logger.info(
            "✅ [视频] 扫描完成 耗时=%.2fs 缩略图=%d 删除=%d 统计: %s",
            cost, self.stats.thumbnails_generated, self.stats.deleted,
            self.stats.to_dict(),
        )
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
        # ⭐ 安全锁：只查视频和目录类型，不碰漫画等
        q = Media.query.filter(
            Media.file_name == name,
            Media.media_type.in_([MediaType.VIDEO, MediaType.DIRECTORY]),  # ⭐ 锁死
        )
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.parent_id.is_(None))
        return q.first()

    def _process_video(self, path: Path, parent_id, rel_path: str):
        file_hash = fast_file_fingerprint(path)
        file_size = path.stat().st_size

        self.scanned_paths.add(rel_path)

        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_hash and existing.relative_path == rel_path:
            # ⭐ 改这里：只传 path 和 hash
            self._ensure_thumbnail(path, file_hash)
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

        # ⭐ 改这里：只传 path 和 hash
        self._ensure_thumbnail(path, file_hash)

        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            logger.info("🎬 新增视频: %s (id=%d)", path.name, media.id)
        else:
            self.stats.updated += 1
            logger.info("🎬 更新视频: %s (id=%d)", path.name, media.id)


    def _process_directory(self, path: Path, parent_id, rel_path: str) -> Optional[int]:
        # ⭐ 记录路径（目录）
        self.scanned_paths.add(rel_path)

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

    # ⭐⭐⭐ 新增：缩略图管理 ⭐⭐⭐
    def _ensure_thumbnail(self, video_path: Path, file_hash: str):
        """只管在磁盘上生成缩略图，不管数据库"""
        thumbnail_name = f"{file_hash}.jpg"
        thumbnail_path = THUMBNAIL_DIR / thumbnail_name

        # 已经有缩略图了就不重复生成
        if thumbnail_path.exists():
            return

        if generate_thumbnail(video_path, thumbnail_path):
            self.stats.thumbnails_generated += 1
            logger.debug("🖼️ 生成缩略图: %s -> %s", video_path.name, thumbnail_name)

    def _cleanup_orphans(self):
        # 拉出可能成为孤儿的视频和目录
        db_records = Media.query.with_entities(
            Media.id, Media.relative_path, Media.file_hash, Media.media_type, Media.is_dir
        ).filter(
            Media.media_type.in_([MediaType.VIDEO, MediaType.DIRECTORY]),
        ).all()

        orphan_video_ids = []
        orphan_dir_ids = []
        orphan_thumbnails = []

        for record in db_records:
            # 磁盘上找不到了，判定为孤儿
            if record.relative_path not in self.scanned_paths:
                if record.media_type == MediaType.VIDEO:
                    orphan_video_ids.append(record.id)
                    if record.file_hash:
                        orphan_thumbnails.append(THUMBNAIL_DIR / f"{record.file_hash}.jpg")
                elif record.is_dir == 1:
                    # 如果是目录，先放进“待定名单”，等会儿做安检
                    orphan_dir_ids.append(record.id)

        if not orphan_video_ids and not orphan_dir_ids:
            return

        # ==========================================
        # ⭐ 核心：过滤目录孤儿，保护被其他模块占用的目录
        # ==========================================
        safe_dir_ids_to_delete = []
        if orphan_dir_ids:
            # 批量查询这些孤儿目录下的【直接子节点】
            # 只要查到哪怕一个 media_type != 3 的子节点，说明里面有漫画/小说等，这个目录就被“污染”了
            non_video_children = Media.query.filter(
                Media.parent_id.in_(orphan_dir_ids),
                Media.media_type != MediaType.VIDEO  # 不是视频的东西
            ).with_entities(Media.parent_id).distinct().all()
            
            # 被污染（受保护）的目录 ID 集合
            protected_dir_ids = {child.parent_id for child in non_video_children}
            
            # 只有干干净净（或者空目录）的孤儿目录，才允许视频模块清理
            safe_dir_ids_to_delete = [did for did in orphan_dir_ids if did not in protected_dir_ids]

        # 合并最终要删除的 ID
        final_ids_to_delete = orphan_video_ids + safe_dir_ids_to_delete
        
        if not final_ids_to_delete:
            return

        # 执行删除（再次锁死 media_type 防万一）
        deleted = Media.query.filter(
            Media.id.in_(final_ids_to_delete),
            Media.media_type.in_([MediaType.VIDEO, MediaType.DIRECTORY]),
        ).delete(synchronize_session=False)

        self.stats.deleted = deleted
        
        protected_count = len(orphan_dir_ids) - len(safe_dir_ids_to_delete)
        if deleted > 0 or protected_count > 0:
            logger.info(
                "🗑️ [视频] 清理孤儿: 删除 %d 条, 保护跳过 %d 个非纯视频目录", 
                deleted, protected_count
            )

        # 清理缩略图
        for thumb_path in orphan_thumbnails:
            try:
                if thumb_path.exists():
                    thumb_path.unlink()
            except OSError:
                pass
