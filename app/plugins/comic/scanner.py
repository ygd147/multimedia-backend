"""
漫画目录扫描器
"""

import hashlib
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image as PILImage

from app.extensions import db
from app.models import Media, MediaImageMeta, MediaZipChild, MediaType, MediaStatus

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

def sha256_file(path: Path, block: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()

def folder_fingerprint(folder: Path) -> str:
    images = sorted(
        (f for f in folder.iterdir() if _is_image(f) and not _should_skip(f.name)),
        key=lambda f: f.name.lower(),
    )
    h = hashlib.sha256()
    for img in images:
        h.update(img.name.encode("utf-8"))
        h.update(str(img.stat().st_size).encode("utf-8"))
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

def _list_zip_images(zip_path: Path) -> list[dict]:
    children = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            entries = [
                info for info in zf.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() in IMAGE_EXTS
            ]
            entries.sort(key=lambda e: Path(e.filename).name.lower())
            for idx, info in enumerate(entries):
                children.append({
                    "file_name": Path(info.filename).name,
                    "file_path": info.filename,
                    "sort_order": idx,
                })
    except (zipfile.BadZipFile, OSError) as e:
        logger.error("读取 ZIP 失败 %s: %s", zip_path, e)
    return children


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

        # 有子目录 → 容器（必须递归）
        if any(e.is_dir() for e in entries):
            return Cat.DIRECTORY

        files = [e for e in entries if e.is_file()]
        has_image   = any(e.suffix.lower() in IMAGE_EXTS   for e in files)
        has_archive = any(e.suffix.lower() in ARCHIVE_EXTS for e in files)

        # 有图片、没有压缩包 → 图片文件夹（叶子节点，不递归）
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
        self._scan_dir(self.base_path, parent_id=None)
        db.session.commit()
        logger.info("✅ 扫描完成  总计=%d 新增=%d 更新=%d 跳过=%d 错误=%d",
                     self.stats.total, self.stats.inserted, self.stats.updated, self.stats.skipped, self.stats.errors)
        return self.stats

    def _scan_dir(self, directory: Path, parent_id: Optional[int]):
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (0 if e.is_dir() else 1, e.name.lower()))
        except (PermissionError, OSError) as e:
            logger.error("无法读取目录 %s: %s", directory, e)
            return

        for entry in entries:
            self.stats.total += 1
            cat = classify(entry)
            # ⭐ 计算相对路径
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

    def _find_existing(self, name: str, parent_id: Optional[int]):
        q = Media.query.filter(Media.file_name == name)
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.parent_id.is_(None))
        return q.first()

    def _process_archive(self, path: Path, parent_id, rel_path: str):
        file_hash = sha256_file(path)
        file_size = path.stat().st_size
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_hash and existing.relative_path == rel_path:
            self.stats.skipped += 1
            return

        media = existing or Media(
            media_type=MediaType.IMAGE, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=0,
        )
        media.file_hash = file_hash
        media.relative_path = rel_path  # ⭐ 保存路径
        media.file_size = file_size
        media.media_type = MediaType.IMAGE
        media.is_dir = 0
        media.status = MediaStatus.READY
        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
        else:
            self.stats.updated += 1

        self._upsert_image_meta(media.id, is_archive=1)
        self._sync_zip_children(media.id, _list_zip_images(path))

    def _process_image_folder(self, path: Path, parent_id, rel_path: str):
        images = _get_sorted_images(path)
        if not images:
            self.stats.skipped += 1
            return
        file_hash = folder_fingerprint(path)
        file_size = sum(f.stat().st_size for f in images)
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_hash and existing.relative_path == rel_path:
            self.stats.skipped += 1
            return

        media = existing or Media(
            media_type=MediaType.IMAGE, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=1, dir_name=path.name,
        )
        media.file_hash = file_hash
        media.relative_path = rel_path  # ⭐ 保存路径
        media.file_size = file_size
        media.media_type = MediaType.IMAGE
        media.is_dir = 1
        media.dir_name = path.name
        media.status = MediaStatus.READY
        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
        else:
            self.stats.updated += 1

        dims = _first_image_dims(path)
        color = _extract_main_color(path)
        self._upsert_image_meta(media.id, is_archive=0, width=dims[0] if dims else None, height=dims[1] if dims else None, main_color=color)

    def _process_directory(self, path: Path, parent_id, rel_path: str) -> Optional[int]:
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.is_dir == 1 and existing.relative_path == rel_path:
            self.stats.skipped += 1
            return existing.id
        if existing:
            existing.is_dir = 1
            existing.dir_name = path.name
            existing.relative_path = rel_path  # ⭐ 保存路径
            existing.media_type = MediaType.DIRECTORY
            existing.status = MediaStatus.READY
            self.stats.updated += 1
            return existing.id

        media = Media(
            file_hash="", media_type=MediaType.DIRECTORY, file_name=path.name,
            file_size=0, status=MediaStatus.READY, parent_id=parent_id,
            is_dir=1, dir_name=path.name, relative_path=rel_path,  # ⭐ 保存路径
        )
        db.session.add(media)
        db.session.flush()
        self.stats.inserted += 1
        return media.id

    def _upsert_image_meta(self, media_id, is_archive, width=None, height=None, main_color=None):
        meta = MediaImageMeta.query.filter_by(media_id=media_id).first()
        if meta:
            meta.is_archive = is_archive
            if width is not None: meta.width = width
            if height is not None: meta.height = height
            if main_color is not None: meta.main_color = main_color
        else:
            db.session.add(MediaImageMeta(
                media_id=media_id, is_archive=is_archive, width=width,
                height=height, thumb_path="", main_color=main_color,
            ))

    def _sync_zip_children(self, media_id, children):
        MediaZipChild.query.filter_by(media_id=media_id).delete()
        for c in children:
            db.session.add(MediaZipChild(
                media_id=media_id, file_name=c["file_name"],
                file_path=c["file_path"], sort_order=c["sort_order"],
            ))
