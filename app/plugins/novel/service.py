"""
小说业务逻辑
"""

from sqlalchemy import text
from app.extensions import db
from app.models import Media, MediaNovelMeta, NovelChapter, MediaType

NOVEL_TYPE = 2

class NovelService:
    @staticmethod
    def list_media(page=1, per_page=20, keyword=None, parent_id=None):
        q_novels = Media.query.filter(Media.media_type == NOVEL_TYPE)
        if parent_id is not None:
            q_novels = q_novels.filter(Media.parent_id == parent_id)
        else:
            q_novels = q_novels.filter(Media.parent_id.is_(None))
        if keyword:
            q_novels = q_novels.filter(Media.file_name.ilike(f'%{keyword}%'))
        direct_novels = q_novels.all()

        q_dirs = Media.query.filter(Media.is_dir == 1)
        if parent_id is not None:
            q_dirs = q_dirs.filter(Media.parent_id == parent_id)
        else:
            q_dirs = q_dirs.filter(Media.parent_id.is_(None))
        direct_dir_records = q_dirs.all()
        direct_dir_ids = [d.id for d in direct_dir_records]
        valid_dir_ids = set()

        if direct_dir_ids:
            pid_condition = "a.parent_id = :target_pid" if parent_id is not None else "a.parent_id IS NULL"
            keyword_condition = "AND v.file_name LIKE :keyword" if keyword else ""
            ids_str = ",".join(map(str, direct_dir_ids))
            
            cte_sql = text(f"""
                WITH RECURSIVE 
                all_descendants AS (
                    SELECT id, parent_id FROM media WHERE parent_id IN ({ids_str})
                    UNION ALL
                    SELECT m.id, m.parent_id FROM media m JOIN all_descendants d ON m.parent_id = d.id
                ),
                novel_nodes AS (
                    SELECT ad.id FROM all_descendants ad JOIN media v ON ad.id = v.id 
                    WHERE v.media_type = {NOVEL_TYPE} {keyword_condition}
                ),
                ancestors AS (
                    SELECT id, parent_id FROM media WHERE id IN (SELECT id FROM novel_nodes)
                    UNION ALL
                    SELECT m.id, m.parent_id FROM media m JOIN ancestors a ON m.id = a.parent_id
                )
                SELECT DISTINCT a.id FROM ancestors a WHERE {pid_condition}
            """)
            params = {"target_pid": parent_id}
            if keyword: params["keyword"] = f'%{keyword}%'
            result = db.session.execute(cte_sql, params).fetchall()
            valid_dir_ids = {row[0] for row in result}

        dir_map = {d.id: d for d in direct_dir_records}
        valid_dirs = [dir_map[did] for did in valid_dir_ids if did in dir_map]
        all_items = valid_dirs + direct_novels
        all_items.sort(key=lambda x: (0 if x.is_dir else 1, x.file_name.lower()))
        
        total = len(all_items)
        start = (page - 1) * per_page
        paginated_items = all_items[start:start+per_page]
        
        return {
            "items": [NovelService._format_item(item) for item in paginated_items],
            "total": total, "page": page, "per_page": per_page,
        }

    @staticmethod
    def _format_item(item):
        data = {
            "id": item.id,
            "file_name": item.file_name,
            "category": "directory" if item.is_dir else "novel",
            "is_dir": item.is_dir,
            "file_size": item.file_size,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        if item.is_dir:
            data["children_count"] = Media.query.filter(
                Media.parent_id == item.id, Media.media_type == NOVEL_TYPE
            ).count()
        else:
            meta = MediaNovelMeta.query.filter_by(media_id=item.id).first()
            data["total_chars"] = meta.total_chars if meta else 0
            data["total_chapters"] = meta.total_chapters if meta else 0
        return data

    @staticmethod
    def get_chapters(media_id: int):
        chapters = NovelChapter.query.filter_by(media_id=media_id).order_by(NovelChapter.chapter_order).all()
        return [{"id": c.id, "chapter_title": c.chapter_title, "order": c.chapter_order,"word_count" : c.word_count} for c in chapters]

    @staticmethod
    def get_chapter_content(chapter_id: int):
        chapter = NovelChapter.query.get(chapter_id)
        if not chapter:
            return None
        return {
            "id": chapter.id,
            "media_id": chapter.media_id,
            "chapter_title": chapter.chapter_title,
            "content": chapter.content
        }
