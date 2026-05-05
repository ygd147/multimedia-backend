"""
漫画目录扫描器（极速版：快速指纹 + 详细耗时日志）
"""

import hashlib
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image as PILImage

from app.extensions import db
from app.models import Media, MediaImageMeta, MediaType, MediaStatus

logger = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".avif",
})
ARCHIVE_EXTS = frozenset({".zip", ".cbz"})
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
    error_details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total, "inserted": self.inserted,
            "updated": self.updated, "skipped": self.skipped,
            "errors": self.errors, "is_running": False,
        }


def _is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS

def _is_archive(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in ARCHIVE_EXTS

def _should_skip(name: str) -> bool:
    return name.startswith(".") or name in IGNORED_NAMES

def fast_file_fingerprint(path: Path, head_bytes: int = 1024 * 1024, tail_bytes: int = 1024 * 1024) -> str:
    """
    快速文件指纹：文件大小 + 修改时间 + 文件头尾部分内容的哈希。
    避免完整读取大文件，速度极快且能检测绝大多数内容变化。
    """
    stat = path.stat()
    size = stat.st_size
    mtime = int(stat.st_mtime * 1000)  # 毫秒级

    # 组合基本信息
    h = hashlib.sha256()
    h.update(str(size).encode())
    h.update(str(mtime).encode())

    # 读取文件头尾部分内容
    try:
        with open(path, "rb") as f:
            # 头部
            head = f.read(head_bytes)
            h.update(head)

            # 尾部（如果文件足够大）
            if size > head_bytes + tail_bytes:
                f.seek(-tail_bytes, 2)
                tail = f.read(tail_bytes)
                h.update(tail)
    except Exception as e:
        logger.warning("读取文件部分内容失败 %s: %s", path, e)

    return h.hexdigest()

def folder_fingerprint(folder: Path) -> str:
    """
    文件夹指纹：基于图片文件名、大小和修改时间。
    """
    images = sorted(
        (f for f in folder.iterdir() if _is_image(f) and not _should_skip(f.name)),
        key=lambda f: f.name.lower(),
    )
    h = hashlib.sha256()
    for img in images:
        stat = img.stat()
        h.update(img.name.encode("utf-8"))
        h.update(str(stat.st_size).encode())
        h.update(str(int(stat.st_mtime * 1000)).encode())
    return h.hexdigest()

def _get_sorted_images(folder: Path) -> list[Path]:
    return sorted(
        (f for f in folder.iterdir() if _is_image(f) and not _should_skip(f.name)),
        key=lambda f: f.name.lower(),
    )

def _first_image_dims(folder: Path) -> Optional[tuple[int, int]]:
    images = _get_sorted_images(folder)
    if not images:
        return None
    try:
        with PILImage.open(images[0]) as img:
            w, h = img.size
            return (w, h) if w > 0 and h > 0 else None
    except Exception as e:
        logger.warning("读取图片尺寸失败 %s: %s", images[0], e)
        return None

def _extract_main_color(folder: Path) -> Optional[str]:
    images = _get_sorted_images(folder)
    if not images:
        return None
    try:
        with PILImage.open(images[0]) as img:
            img.thumbnail((32, 32))
            pixels = list(img.convert("RGB").getdata())
            if not pixels:
                return None
            r = sum(p[0] for p in pixels) // len(pixels)
            g = sum(p[1] for p in pixels) // len(pixels)
            b = sum(p[2] for p in pixels) // len(pixels)
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None

def _count_zip_pages(zip_path: Path) -> int:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return sum(
                1 for info in zf.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_EXTS
            )
    except (zipfile.BadZipFile, OSError) as e:
        logger.error("读取 ZIP 失败 %s: %s", zip_path, e)
        return 0


class Cat:
    ARCHIVE = "archive"
    IMAGE_FOLDER = "image_folder"
    DIRECTORY = "directory"
    SKIP = "skip"


def classify(path: Path) -> str:
    if _should_skip(path.name):
        return Cat.SKIP
    if path.is_file():
        return Cat.ARCHIVE if _is_archive(path) else Cat.SKIP
    if path.is_dir():
        try:
            entries = [e for e in path.iterdir() if not _should_skip(e.name)]
        except (PermissionError, OSError):
            return Cat.SKIP
        if not entries:
            return Cat.DIRECTORY
        if any(e.is_dir() for e in entries):
            return Cat.DIRECTORY
        files = [e for e in entries if e.is_file()]
        has_image   = any(e.suffix.lower() in IMAGE_EXTS   for e in files)
        has_archive = any(e.suffix.lower() in ARCHIVE_EXTS for e in files)
        if has_image and not has_archive:
            return Cat.IMAGE_FOLDER
        return Cat.DIRECTORY
    return Cat.SKIP


class ComicScanner:
    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).resolve()
        self.stats = ScanStats()

    def scan(self) -> ScanStats:
        if not self.base_path.is_dir():
            raise FileNotFoundError(f"目录不存在: {self.base_path}")
        logger.info("🚀 开始扫描: %s", self.base_path)
        scan_start = time.perf_counter()
        self._scan_dir(self.base_path, parent_id=None)
        db.session.commit()
        total_time = time.perf_counter() - scan_start
        logger.info(
            "✅ 扫描完成  总耗时=%.2fs  总计=%d 新增=%d 更新=%d 跳过=%d 错误=%d",
            total_time, self.stats.total, self.stats.inserted, self.stats.updated,
            self.stats.skipped, self.stats.errors
        )
        return self.stats

    def _scan_dir(self, directory: Path, parent_id: Optional[int]):
        dir_start = time.perf_counter()
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (0 if e.is_dir() else 1, e.name.lower()))
        except (PermissionError, OSError) as e:
            logger.error("无法读取目录 %s: %s", directory, e)
            return

        rel = directory.relative_to(self.base_path) if directory != self.base_path else "."
        logger.info("📂 扫描目录 [%s]  共 %d 项", rel, len(entries))

        for entry in entries:
            self.stats.total += 1
            cat = classify(entry)
            try:
                rel_path = str(entry.relative_to(self.base_path))
            except ValueError:
                rel_path = entry.name

            try:
                if cat == Cat.ARCHIVE:
                    self._process_archive(entry, parent_id, rel_path)
                elif cat == Cat.IMAGE_FOLDER:
                    self._process_image_folder(entry, parent_id, rel_path)
                elif cat == Cat.DIRECTORY:
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
                logger.info(
                    "⏳ 进度: 已处理 %d 项 (新增 %d / 更新 %d / 跳过 %d / 错误 %d)",
                    self.stats.total, self.stats.inserted, self.stats.updated,
                    self.stats.skipped, self.stats.errors,
                )

        total_dir_time = time.perf_counter() - dir_start
        logger.info("📂 目录扫描完成 [%s] 耗时 %.2fs", rel, total_dir_time)

    def _find_existing(self, name: str, parent_id: Optional[int]):
        q = Media.query.filter(Media.file_name == name)
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.parent_id.is_(None))
        return q.first()

    def _process_archive(self, path: Path, parent_id, rel_path: str):
        start = time.perf_counter()
        logger.info("📦 处理压缩包: %s", path.name)

        # 快速指纹（大小 + 修改时间 + 头尾哈希）
        fp_start = time.perf_counter()
        file_fingerprint = fast_file_fingerprint(path)
        fp_cost = time.perf_counter() - fp_start
        logger.info("   └─ 快速指纹: %.2fs", fp_cost)

        file_size = path.stat().st_size

        # 统计页数
        count_start = time.perf_counter()
        page_count = _count_zip_pages(path)
        count_cost = time.perf_counter() - count_start
        logger.info("   └─ 页数统计: %.2fs (%d 页)", count_cost, page_count)

        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_fingerprint and existing.relative_path == rel_path:
            self.stats.skipped += 1
            logger.info("   └─ 跳过 (内容未变化)")
            return

        media = existing or Media(
            media_type=MediaType.IMAGE, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=0,
        )
        media.file_hash = file_fingerprint
        media.relative_path = rel_path
        media.file_size = file_size
        media.media_type = MediaType.IMAGE
        media.is_dir = 0
        media.status = MediaStatus.READY

        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            logger.info("   └─ 新增记录, media_id=%d", media.id)
        else:
            self.stats.updated += 1
            logger.info("   └─ 更新记录, media_id=%d", media.id)

        self._upsert_image_meta(media.id, is_archive=1, page_count=page_count)

        total_cost = time.perf_counter() - start
        logger.info("✅ 压缩包处理完成 [%s] 总耗时 %.2fs", path.name, total_cost)

    def _process_image_folder(self, path: Path, parent_id, rel_path: str):
        start = time.perf_counter()
        logger.info("🖼️ 处理图片目录: %s", path.name)

        # 获取图片列表
        list_start = time.perf_counter()
        images = _get_sorted_images(path)
        list_cost = time.perf_counter() - list_start
        logger.info("   └─ 列出图片: %.2fs (%d 张)", list_cost, len(images))
        if not images:
            self.stats.skipped += 1
            logger.info("   └─ 跳过 (无图片)")
            return

        # 文件夹指纹
        fp_start = time.perf_counter()
        file_fingerprint = folder_fingerprint(path)
        fp_cost = time.perf_counter() - fp_start
        logger.info("   └─ 文件夹指纹: %.2fs", fp_cost)

        file_size = sum(f.stat().st_size for f in images)

        # 提取尺寸
        dims_start = time.perf_counter()
        dims = _first_image_dims(path)
        dims_cost = time.perf_counter() - dims_start
        logger.info("   └─ 提取尺寸: %.2fs", dims_cost)

        # 提取主色
        color_start = time.perf_counter()
        color = _extract_main_color(path)
        color_cost = time.perf_counter() - color_start
        logger.info("   └─ 提取主色: %.2fs", color_cost)

        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_fingerprint and existing.relative_path == rel_path:
            self.stats.skipped += 1
            logger.info("   └─ 跳过 (内容未变化)")
            return

        media = existing or Media(
            media_type=MediaType.IMAGE, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=1, dir_name=path.name,
        )
        media.file_hash = file_fingerprint
        media.relative_path = rel_path
        media.file_size = file_size
        media.media_type = MediaType.IMAGE
        media.is_dir = 1
        media.dir_name = path.name
        media.status = MediaStatus.READY

        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            logger.info("   └─ 新增记录, media_id=%d", media.id)
        else:
            self.stats.updated += 1
            logger.info("   └─ 更新记录, media_id=%d", media.id)

        self._upsert_image_meta(
            media.id, is_archive=0, page_count=len(images),
            width=dims[0] if dims else None,
            height=dims[1] if dims else None,
            main_color=color
        )

        total_cost = time.perf_counter() - start
        logger.info("✅ 图片目录处理完成 [%s] 总耗时 %.2fs", path.name, total_cost)

    def _process_directory(self, path: Path, parent_id, rel_path: str) -> Optional[int]:
        start = time.perf_counter()
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.is_dir == 1 and existing.relative_path == rel_path:
            self.stats.skipped += 1
            logger.debug("📁 目录已存在 [%s] (跳过)", path.name)
            return existing.id

        if existing:
            existing.is_dir = 1
            existing.dir_name = path.name
            existing.relative_path = rel_path
            existing.media_type = MediaType.DIRECTORY
            existing.status = MediaStatus.READY
            self.stats.updated += 1
            media_id = existing.id
            logger.info("📁 更新目录 [%s] media_id=%d", path.name, media_id)
        else:
            media = Media(
                file_hash="", media_type=MediaType.DIRECTORY, file_name=path.name,
                file_size=0, status=MediaStatus.READY, parent_id=parent_id,
                is_dir=1, dir_name=path.name, relative_path=rel_path,
            )
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            media_id = media.id
            logger.info("📁 新增目录 [%s] media_id=%d", path.name, media_id)

        total_cost = time.perf_counter() - start
        logger.debug("📁 目录处理完成 [%s] 耗时 %.3fs", path.name, total_cost)
        return media_id

    def _upsert_image_meta(self, media_id, is_archive, page_count=0, width=None, height=None, main_color=None):
        meta = MediaImageMeta.query.filter_by(media_id=media_id).first()
        if meta:
            meta.is_archive = is_archive
            meta.page_count = page_count
            if width is not None:
                meta.width = width
            if height is not None:
                meta.height = height
            if main_color is not None:
                meta.main_color = main_color
        else:
            db.session.add(MediaImageMeta(
                media_id=media_id, is_archive=is_archive, page_count=page_count,
                width=width, height=height, thumb_path="", main_color=main_color,
            ))