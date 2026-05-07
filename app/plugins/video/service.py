"""Video 业务逻辑层"""

import logging
from pathlib import Path
from typing import Optional

from app.extensions import db
from app.config import Config
from app.models import Media, MediaType
from sqlalchemy import text
logger = logging.getLogger(__name__)

VIDEO_TYPE = 3 
DIR_TYPE = 0 
DIR_IS = 1 

class VideoService:

    @staticmethod
    def list_media(page=1, per_page=20, keyword=None, parent_id=None):
        """
        获取视频列表（终极版：严格通过 media_type=3 反推目录，完美过滤漫画目录，支持无限极层级）
        """
        
        # ==========================================
        # 1. 查出当前层级【直接包含】的视频文件
        # ==========================================
        q_videos = Media.query.filter(Media.media_type == VIDEO_TYPE)
        if parent_id is not None:
            q_videos = q_videos.filter(Media.parent_id == parent_id)
        else:
            q_videos = q_videos.filter(Media.parent_id.is_(None))
            
        if keyword:
            q_videos = q_videos.filter(Media.file_name.ilike(f'%{keyword}%'))
            
        direct_videos = q_videos.all()

        # ==========================================
        # 2. 查出当前层级【直接包含】的所有目录（不管它是什么类型建的）
        # ==========================================
        q_dirs = Media.query.filter(Media.is_dir == 1)
        if parent_id is not None:
            q_dirs = q_dirs.filter(Media.parent_id == parent_id)
        else:
            q_dirs = q_dirs.filter(Media.parent_id.is_(None))
            
        direct_dir_records = q_dirs.all()
        direct_dir_ids = [d.id for d in direct_dir_records]
        
        valid_dir_ids = set()
        
        # ==========================================
        # 3. 核心逻辑：递归向下找视频，向上回溯找有效目录
        # ==========================================
        if direct_dir_ids:
            # 拼接 SQL 条件（处理 parent_id 为 NULL 的情况）
            if parent_id is not None:
                pid_condition = "a.parent_id = :target_pid"
            else:
                pid_condition = "a.parent_id IS NULL"
                
            # 拼接搜索条件
            keyword_condition = ""
            if keyword:
                keyword_condition = "AND v.file_name LIKE :keyword"

            # 将 ID 列表转为安全的 SQL IN 字符串 (全是整数，无注入风险)
            ids_str = ",".join(map(str, direct_dir_ids))
            
            # 递归 CTE SQL
            cte_sql = text(f"""
                WITH RECURSIVE 
                -- 第一步：向下递归，找出所有直接子目录的【全部后代节点】
                all_descendants AS (
                    SELECT id, parent_id FROM media WHERE parent_id IN ({ids_str})
                    UNION ALL
                    SELECT m.id, m.parent_id FROM media m 
                    JOIN all_descendants d ON m.parent_id = d.id
                ),
                -- 第二步：在全部后代中，找出真正的视频节点
                video_nodes AS (
                    SELECT ad.id FROM all_descendants ad 
                    JOIN media v ON ad.id = v.id 
                    WHERE v.media_type = 3 {keyword_condition}
                ),
                -- 第三步：从视频节点向上递归，找出它们的所有【祖先节点】
                ancestors AS (
                    SELECT id, parent_id FROM media WHERE id IN (SELECT id FROM video_nodes)
                    UNION ALL
                    SELECT m.id, m.parent_id FROM media m 
                    JOIN ancestors a ON m.id = a.parent_id
                )
                -- 第四步：在祖先节点中，只筛选出【直接属于当前查询层级】的目录
                SELECT DISTINCT a.id FROM ancestors a WHERE {pid_condition}
            """)
            
            params = {"target_pid": parent_id}
            if keyword:
                params["keyword"] = f'%{keyword}%'
                
            result = db.session.execute(cte_sql, params).fetchall()
            valid_dir_ids = {row[0] for row in result}

        # ==========================================
        # 4. 组装数据并排序
        # ==========================================
        # 从刚才查出的直接目录里，过滤出被视频“背书”过的有效目录
        dir_map = {d.id: d for d in direct_dir_records}
        valid_dirs = [dir_map[did] for did in valid_dir_ids if did in dir_map]
        
        # 合并：有效目录 + 直接视频
        all_items = valid_dirs + direct_videos
        
        # 排序：目录永远在前，文件在后，同级按名字排序
        all_items.sort(key=lambda x: (0 if x.is_dir else 1, x.file_name.lower()))
        
        # ==========================================
        # 5. 内存分页
        # ==========================================
        # 由于经过了复杂的跨层级过滤，直接在 Python 中切片分页是最稳妥且性能足够的
        # （一个页面的同级文件/文件夹通常不会超过几百个）
        total = len(all_items)
        start = (page - 1) * per_page
        end = start + per_page
        paginated_items = all_items[start:end]
        
        return {
            "items": [VideoService._format_item(item) for item in paginated_items],
            "total": total,
            "page": page,
            "per_page": per_page,
        }


    @staticmethod
    def _format_item(item):
        """统一格式化输出字段"""
        data = {
            "id": item.id,
            "file_name": item.file_name,
            "file_hash": getattr(item, 'file_hash', None),
            "category": "directory" if item.is_dir else "video",
            "is_dir": item.is_dir,
            "file_size": item.file_size,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        
        if item.is_dir:
            # 统计该目录【直接下一层】包含的视频数量
            data["children_count"] = Media.query.filter(
                Media.parent_id == item.id,
                Media.media_type == VIDEO_TYPE
            ).count()
            
        return data

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
