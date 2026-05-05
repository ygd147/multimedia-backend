"""
ORM 模型定义
"""

from sqlalchemy import (
    Column, BigInteger, String, Integer, SmallInteger, DateTime,
    Index,
)
from sqlalchemy.sql import func

from app.extensions import db


class MediaType:
    DIRECTORY = 0
    IMAGE    = 1
    NOVEL    = 2
    VIDEO    = 3


class MediaStatus:
    PENDING    = 0
    PROCESSING = 1
    READY      = 2
    FAILED     = 3


class Media(db.Model):
    __tablename__ = "media"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    file_hash     = Column(String(64),  nullable=False, default="")
    media_type    = Column(SmallInteger, nullable=False, comment="0=目录 1=图片 2=小说 3=视频")
    file_name     = Column(String(512), nullable=False)
    relative_path = Column(String(1024),nullable=False, default="", comment="相对路径")
    file_size     = Column(BigInteger,   nullable=False, default=0)
    status        = Column(SmallInteger, nullable=False, default=MediaStatus.PENDING)
    parent_id     = Column(BigInteger,   nullable=True, default=None)
    is_dir        = Column(SmallInteger, nullable=False, default=0)
    dir_name      = Column(String(255),  nullable=True, default=None)
    created_at    = Column(DateTime,     nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_file_hash",      "file_hash"),
        Index("idx_media_type",     "media_type"),
        Index("idx_status",         "status"),
        Index("idx_parent_id",      "parent_id"),
        Index("idx_created_at",     "created_at"),
        Index("idx_type_status",    "media_type", "status"),
        Index("idx_parent_created", "parent_id", "created_at"),
    )


class MediaImageMeta(db.Model):
    __tablename__ = "media_image_meta"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    media_id   = Column(BigInteger, nullable=False, unique=True)
    width      = Column(Integer, nullable=True, default=None)
    height     = Column(Integer, nullable=True, default=None)
    thumb_path = Column(String(512), nullable=False, default="")
    is_archive = Column(SmallInteger, nullable=False, default=0)
    main_color = Column(String(32), nullable=True, default=None)
    page_count = db.Column(db.Integer, nullable=True, comment='总页数（压缩包或文件夹）')

# class MediaZipChild(db.Model):
#     __tablename__ = "media_zip_child"

#     id         = Column(BigInteger, primary_key=True, autoincrement=True)
#     media_id   = Column(BigInteger, nullable=False, index=True)
#     file_name  = Column(String(512), nullable=False)
#     thumb_path = Column(String(512), nullable=False, default="")
#     file_path  = Column(String(512), nullable=False, default="")
#     width      = Column(Integer, nullable=True, default=None)
#     height     = Column(Integer, nullable=True, default=None)
#     sort_order = Column(Integer, nullable=False, default=0)

#     __table_args__ = (
#         Index("idx_media_sort", "media_id", "sort_order"),
#     )


class MediaNovelMeta(db.Model):
    __tablename__ = "media_novel_meta"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    media_id   = Column(BigInteger, nullable=False, unique=True)
    author     = Column(String(255), nullable=False, default="")
    word_count = Column(Integer, nullable=False, default=0)
    encoding   = Column(String(32), nullable=False, default="")


class MediaVideoMeta(db.Model):
    __tablename__ = "media_video_meta"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    media_id      = Column(BigInteger, nullable=False, unique=True)
    duration      = Column(Integer, nullable=False, default=0)
    resolution    = Column(String(32), nullable=False, default="")
    video_codec   = Column(String(64), nullable=False, default="")
    cover_path    = Column(String(512), nullable=False, default="")
    is_transcoded = Column(SmallInteger, nullable=False, default=0)

    __table_args__ = (
        Index("idx_transcoded", "is_transcoded"),
    )
