"""
小说目录扫描器（极速版：漫画架构平替 + 仅小说类型脏数据清理）
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.extensions import db
from app.models import Media, MediaNovelMeta, NovelChapter, MediaType, MediaStatus

logger = logging.getLogger(__name__)

NOVEL_EXT = ".txt"
IGNORED_NAMES = frozenset({
    "__pycache__", ".git", ".svn", ".DS_Store", ".thumbnails",
    "node_modules", ".idea", "@eaDir", "System Volume Information",
})

# 智能分章正则
CHAPTER_PATTERN = re.compile(
    r'^(第[一二三四五六七八九十百千万零〇\d]+[章节回卷集部篇]\s*.*'
    r'|卷[一二三四五六七八九十百千万零〇\d]+\s*.*'
    r'|序[章言]\s*.*|楔子\s*.*|尾声\s*.*|番外\s*.*)',
    re.MULTILINE | re.IGNORECASE
)
FALLBACK_SPLIT_SIZE = 3000


@dataclass
class ScanStats:
    total: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: int = 0
    error_details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total, "inserted": self.inserted,
            "updated": self.updated, "skipped": self.skipped,
            "deleted": self.deleted, "errors": self.errors,
            "is_running": False,
        }


def _should_skip(name: str) -> bool:
    return name.startswith(".") or name in IGNORED_NAMES

def fast_file_fingerprint(path: Path, head_bytes: int = 1024 * 1024) -> str:
    """快速文件指纹：大小 + mtime + 头部1MB"""
    stat = path.stat()
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(str(int(stat.st_mtime * 1000)).encode())
    try:
        with open(path, "rb") as f:
            h.update(f.read(head_bytes))
    except Exception as e:
        logger.warning("读取文件部分内容失败 %s: %s", path, e)
    return h.hexdigest()

def _read_txt_safe(path: Path) -> str:
    for encoding in ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""

def _split_chapters(text: str) -> list[dict]:
    text = text.strip()
    if not text: return []
    matches = list(CHAPTER_PATTERN.finditer(text))
    if len(matches) >= 2:
        chapters = []
        for i in range(len(matches)):
            start_pos = matches[i].start()
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(text)
            title = matches[i].group(1).strip()
            content = text[start_pos:end_pos].split('\n', 1)[-1].strip()
            if content: chapters.append({"title": title, "content": content})
        return chapters if chapters else [{"title": "全文", "content": text}]
    elif len(matches) == 1:
        first_title = matches[0].group(1).strip()
        remaining_text = text[matches[0].end():].strip()
        if not remaining_text: return [{"title": first_title, "content": text}]
        fallback_chaps = _fallback_split(remaining_text)
        if fallback_chaps: fallback_chaps[0]["title"] = first_title
        return fallback_chaps if fallback_chaps else [{"title": first_title, "content": remaining_text}]
    else:
        return _fallback_split(text)

def _fallback_split(text: str) -> list[dict]:
    chapters = []
    for i in range(0, len(text), FALLBACK_SPLIT_SIZE):
        part_text = text[i:i+FALLBACK_SPLIT_SIZE].strip()
        if part_text:
            chapters.append({"title": f"第{i // FALLBACK_SPLIT_SIZE + 1}部分", "content": part_text})
    return chapters if chapters else [{"title": "全文", "content": text}]


class Cat:
    NOVEL = "novel"
    DIRECTORY = "directory"
    SKIP = "skip"

def classify(path: Path) -> str:
    if _should_skip(path.name): return Cat.SKIP
    if path.is_file() and path.suffix.lower() == NOVEL_EXT: return Cat.NOVEL
    if path.is_dir(): return Cat.DIRECTORY
    return Cat.SKIP


class NovelScanner:
    def __init__(self, base_path: str | Path, dry_run: bool = False):
        self.base_path = Path(base_path).resolve()
        self.stats = ScanStats()
        self.valid_media_ids: set[int] = set()
        self.dry_run = dry_run

    def scan(self) -> ScanStats:
        if not self.base_path.is_dir():
            raise FileNotFoundError(f"目录不存在: {self.base_path}")
        logger.info("🚀 [小说] 开始扫描: %s (干跑模式=%s)", self.base_path, self.dry_run)
        scan_start = time.perf_counter()

        self.stats = ScanStats()
        self.valid_media_ids.clear()

        self._scan_dir(self.base_path, parent_id=None)
        self._clean_expired_novel_data()
        db.session.commit()

        total_time = time.perf_counter() - scan_start
        logger.info(
            "✅ [小说] 全流程完成  总耗时=%.2fs  总计=%d 新增=%d 更新=%d 跳过=%d 删除=%d 错误=%d",
            total_time, self.stats.total, self.stats.inserted, self.stats.updated,
            self.stats.skipped, self.stats.deleted, self.stats.errors
        )
        return self.stats

    def _scan_dir(self, directory: Path, parent_id: Optional[int]):
        dir_start = time.perf_counter()
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (0 if e.is_dir() else 1, e.name.lower()))
        except (PermissionError, OSError) as e:
            logger.error("[小说] 无法读取目录 %s: %s", directory, e)
            return

        rel = directory.relative_to(self.base_path) if directory != self.base_path else "."
        logger.info("📂 [小说] 扫描目录 [%s]  共 %d 项", rel, len(entries))

        for entry in entries:
            self.stats.total += 1
            cat = classify(entry)
            try:
                rel_path = str(entry.relative_to(self.base_path))
            except ValueError:
                rel_path = entry.name

            try:
                if cat == Cat.NOVEL:
                    self._process_novel(entry, parent_id, rel_path)
                elif cat == Cat.DIRECTORY:
                    dir_id = self._process_directory(entry, parent_id, rel_path)
                    if dir_id is not None:
                        self._scan_dir(entry, parent_id=dir_id)
                else:
                    self.stats.skipped += 1
            except Exception as e:
                logger.error("[小说] 处理失败 [%s] %s: %s", cat, entry, e, exc_info=True)
                self.stats.errors += 1

            if self.stats.total % 50 == 0:
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                logger.info(
                    "⏳ [小说] 进度: 已处理 %d 项 (新增 %d / 更新 %d / 跳过 %d / 错误 %d)",
                    self.stats.total, self.stats.inserted, self.stats.updated,
                    self.stats.skipped, self.stats.errors,
                )

        total_dir_time = time.perf_counter() - dir_start
        logger.info("📂 [小说] 目录扫描完成 [%s] 耗时 %.2fs", rel, total_dir_time)

    def _find_existing(self, name: str, parent_id: Optional[int]):
        q = Media.query.filter(Media.file_name == name)
        if parent_id is not None:
            q = q.filter(Media.parent_id == parent_id)
        else:
            q = q.filter(Media.parent_id.is_(None))
        return q.first()

    def _process_novel(self, path: Path, parent_id, rel_path: str):
        start = time.perf_counter()
        logger.info("📖 [小说] 处理文件: %s", path.name)

        fp_start = time.perf_counter()
        file_fingerprint = fast_file_fingerprint(path)
        fp_cost = time.perf_counter() - fp_start
        logger.info("   └─ 快速指纹: %.2fs", fp_cost)

        file_size = path.stat().st_size

        existing = self._find_existing(path.name, parent_id)
        if existing and existing.file_hash == file_fingerprint and existing.relative_path == rel_path:
            self.stats.skipped += 1
            self.valid_media_ids.add(existing.id)
            logger.info("   └─ 跳过 (内容未变化)")
            return

        read_start = time.perf_counter()
        text = _read_txt_safe(path)
        read_cost = time.perf_counter() - read_start
        logger.info("   └─ 文件读取: %.2fs (%d 字)", read_cost, len(text))

        split_start = time.perf_counter()
        chapters = _split_chapters(text)
        split_cost = time.perf_counter() - split_start
        logger.info("   └─ 智能分章: %.2fs (拆分 %d 章)", split_cost, len(chapters))

        media = existing or Media(
            media_type=MediaType.NOVEL, file_name=path.name, status=MediaStatus.READY,
            parent_id=parent_id, is_dir=0,
        )
        media.file_hash = file_fingerprint
        media.relative_path = rel_path
        media.file_size = file_size
        media.media_type = MediaType.NOVEL
        media.is_dir = 0
        media.status = MediaStatus.READY

        if not existing:
            db.session.add(media)
            db.session.flush()
            self.stats.inserted += 1
            logger.info("   └─ 新增记录, media_id=%d", media.id)
        else:
            self.stats.updated += 1
            NovelChapter.query.filter_by(media_id=media.id).delete()
            logger.info("   └─ 更新记录(清空旧章节), media_id=%d", media.id)

        self._upsert_novel_meta(media.id, total_chapters=len(chapters), total_chars=len(text))
        for idx, chap in enumerate(chapters):
            db.session.add(NovelChapter(
                media_id=media.id,
                chapter_title=chap["title"][:255],   # ⭐ 安全截断
                chapter_order=idx + 1,
                content=chap["content"],
                word_count=len(chap["content"]) 
            ))
            if (idx + 1) % 50 == 0:
                db.session.flush()
        db.session.flush()
        
        self.valid_media_ids.add(media.id)

        total_cost = time.perf_counter() - start
        logger.info("✅ [小说] 处理完成 [%s] 总耗时 %.2fs", path.name, total_cost)

    def _process_directory(self, path: Path, parent_id, rel_path: str) -> Optional[int]:
        existing = self._find_existing(path.name, parent_id)
        if existing and existing.is_dir == 1 and existing.relative_path == rel_path:
            self.stats.skipped += 1
            self.valid_media_ids.add(existing.id)
            return existing.id

        if existing:
            existing.is_dir = 1; existing.dir_name = path.name; existing.relative_path = rel_path
            existing.media_type = MediaType.DIRECTORY; existing.status = MediaStatus.READY
            self.stats.updated += 1
            media_id = existing.id
        else:
            media = Media(
                file_hash="", media_type=MediaType.DIRECTORY, file_name=path.name,
                file_size=0, status=MediaStatus.READY, parent_id=parent_id,
                is_dir=1, dir_name=path.name, relative_path=rel_path,
            )
            db.session.add(media); db.session.flush()
            self.stats.inserted += 1
            media_id = media.id

        self.valid_media_ids.add(media_id)
        return media_id

    # 【重写核心：严格仅清理media_type=2的小说数据】
    def _clean_expired_novel_data(self):
        """脏数据清理核心逻辑，严格限制：仅操作 media_type=2 的记录"""
        logger.info("🧹 [小说] 开始仅小说类型脏数据清理...")
        clean_start = time.perf_counter()

        all_novel_records = Media.query.filter(Media.media_type == MediaType.NOVEL).all()
        if not all_novel_records:
            logger.info("✅ [小说] 数据库中无小说类型记录，无需清理")
            return

        scan_related_novel_ids = set()
        for record in all_novel_records:
            if not record.relative_path or record.relative_path == ".": continue
            try:
                record_full_path = self.base_path / record.relative_path
                if self.base_path in record_full_path.parents:
                    scan_related_novel_ids.add(record.id)
            except Exception as e:
                logger.warning("[小说] 跳过路径校验异常的记录 media_id=%d: %s", record.id, e)
                continue

        if not scan_related_novel_ids:
            logger.info("✅ [小说] 本次扫描目录下无小说类型记录，无需清理")
            return

        if not self.valid_media_ids:
            logger.warning("⚠️ [小说] 本次扫描未发现任何有效文件，为防止误删，跳过清理流程")
            return

        expired_novel_ids = scan_related_novel_ids - self.valid_media_ids
        if not expired_novel_ids:
            logger.info("✅ [小说] 无过期小说数据，无需清理")
            return

        logger.warning("⚠️ [小说] 发现 %d 条过期小说记录，待清理ID: %s", len(expired_novel_ids), sorted(list(expired_novel_ids)))

        if self.dry_run:
            logger.info("🔕 [小说] 干跑模式已开启，不执行实际删除操作")
            return

        try:
            meta_deleted_count = MediaNovelMeta.query.filter(
                MediaNovelMeta.media_id.in_(expired_novel_ids)
            ).delete(synchronize_session=False)

            chap_deleted_count = NovelChapter.query.filter(
                NovelChapter.media_id.in_(expired_novel_ids)
            ).delete(synchronize_session=False)

            media_deleted_count = Media.query.filter(
                Media.id.in_(expired_novel_ids),
                Media.media_type == MediaType.NOVEL
            ).delete(synchronize_session=False)

            db.session.flush()
            self.stats.deleted = media_deleted_count
            logger.info(
                "✅ [小说] 清理完成：删除小说主记录 %d 条，关联元数据 %d 条，关联章节 %d 条",
                media_deleted_count, meta_deleted_count, chap_deleted_count
            )
        except Exception as e:
            logger.error("❌ [小说] 清理过期小说数据失败: %s", e, exc_info=True)
            db.session.rollback()

        total_clean_time = time.perf_counter() - clean_start
        logger.info("🧹 [小说] 数据清理任务结束，耗时 %.2fs", total_clean_time)

    def _upsert_novel_meta(self, media_id, total_chapters=0, total_chars=0):
        meta = MediaNovelMeta.query.filter_by(media_id=media_id).first()
        if meta:
            meta.total_chapters = total_chapters
            meta.total_chars = total_chars
        else:
            db.session.add(MediaNovelMeta(
                media_id=media_id, total_chapters=total_chapters, total_chars=total_chars
            ))
