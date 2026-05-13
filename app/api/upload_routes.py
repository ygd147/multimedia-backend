import os
import hashlib
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from app.models.db import db
from app.models.resource import MediaResource, MediaComic, MediaNovel, MediaVideo
from app.config import Config

# 独立的上传蓝图，前缀设为 /api
upload_bp = Blueprint('upload', __name__, url_prefix='/api')

# 允许的文件扩展名映射
ALLOWED_EXTENSIONS = {
    'media': {'.jpg', '.jpeg', '.png', '.webp', '.zip'},
    'novel': {'.txt', '.epub'},
    'video': {'.mp4', '.mkv', '.avi', '.mov'}
}

# media_type 枚举映射
TYPE_MAP = {
    'media': Config.TYPE_COMIC,
    'novel': Config.TYPE_NOVEL,
    'video': Config.TYPE_VIDEO
}

def allowed_file(filename, type_key):
    return '.' in filename and \
           os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS.get(type_key, set())

def format_upload_response(res):
    """格式化返回数据，严格匹配前端接口文档"""
    type_map_str = {1: 'image', 2: 'text', 3: 'video'}
    category_map = {1: 'image', 2: 'novel', 3: 'video'}
    
    is_dir = 0
    if res.media_type == Config.TYPE_COMIC:
        comic = db.session.get(MediaComic, res.id)
        if comic and comic.is_archive == -1: is_dir = 1
    elif res.file_size == 0:
        is_dir = 1

    return {
        "id": res.id,
        "file_hash": res.file_hash,
        "media_type": type_map_str.get(res.media_type, 'unknown'),
        "file_name": res.file_name,
        "file_size": res.file_size,
        "status": "active",
        "parent_id": res.parent_id,
        "is_dir": is_dir,
        "category": category_map.get(res.media_type, 'unknown'),
        "created_at": res.created_at.isoformat() + "Z" if res.created_at else None
    }

@upload_bp.route('/<type_key>/upload', methods=['POST'])
def upload_files(type_key):
    if type_key not in TYPE_MAP:
        return jsonify({"code": 400, "msg": "Invalid media type", "data": None}), 400

    target_media_type = TYPE_MAP[type_key]
    
    # 1. 获取参数
    files = request.files.getlist('files')
    parent_id_str = request.form.get('parent_id')
    
    if not files or files[0].filename == '':
        return jsonify({"code": 400, "msg": "No files selected", "data": None}), 400

    # 2. 目录校验
    parent_id = None
    save_dir = None
    
    if parent_id_str and parent_id_str.lower() != 'null':
        try:
            parent_id = int(parent_id_str)
        except ValueError:
            return jsonify({"code": 400, "msg": "Invalid parent_id", "data": None}), 400

        parent_res = db.session.get(MediaResource, parent_id)
        if not parent_res:
            return jsonify({"code": 404, "msg": "Target directory not found", "data": None}), 404

        # 校验是否为目录
        is_dir = False
        if parent_res.media_type == Config.TYPE_COMIC:
            comic_ext = db.session.get(MediaComic, parent_res.id)
            if comic_ext and comic_ext.is_archive == -1: is_dir = True
        else:
            if parent_res.file_size == 0: is_dir = True
                
        if not is_dir:
            return jsonify({"code": 400, "msg": "Target is not a directory", "data": None}), 400

        # 校验类型匹配
        if parent_res.media_type != target_media_type:
            return jsonify({"code": 400, "msg": "Type mismatch with parent directory", "data": None}), 400

        save_dir = parent_res.file_path
    else:
        # 挂载在根目录
        if target_media_type == Config.TYPE_COMIC: save_dir = Config.COMIC_DIR
        elif target_media_type == Config.TYPE_NOVEL: save_dir = Config.NOVEL_DIR
        elif target_media_type == Config.TYPE_VIDEO: save_dir = Config.VIDEO_DIR

    # 3. 处理文件
    created_items = []
    
    for file in files:
        filename = secure_filename(file.filename)
        if not allowed_file(filename, type_key):
            return jsonify({"code": 400, "msg": f"Unsupported file type: {filename}", "data": None}), 400

        # 读取文件流并计算哈希
        file_data = file.read()
        file_hash = hashlib.sha256(file_data).hexdigest()
        file_size = len(file_data)

        # 按哈希前2位建立物理子目录
        sub_dir = os.path.join(save_dir, file_hash[:2])
        os.makedirs(sub_dir, exist_ok=True)
        
        # 最终绝对路径
        abs_path = os.path.join(sub_dir, filename)
        
        # 写入磁盘
        with open(abs_path, 'wb') as f:
            f.write(file_data)

        # 4. 写入数据库
        res = MediaResource(
            file_hash=file_hash,
            media_type=target_media_type,
            file_name=filename,
            file_path=abs_path,
            file_size=file_size,
            status=Config.STATUS_READY, # 直接就绪，不解压
            parent_id=parent_id
        )
        db.session.add(res)
        db.session.flush()

        title = os.path.splitext(filename)[0]

        if target_media_type == Config.TYPE_COMIC:
            ext = os.path.splitext(filename)[1].lower()
            is_archive = 1 if ext == '.zip' else 0
            db.session.add(MediaComic(id=res.id, title=title, is_archive=is_archive, page_count=0))
            
        elif target_media_type == Config.TYPE_NOVEL:
            db.session.add(MediaNovel(id=res.id, title=title, word_count=0))
            
        elif target_media_type == Config.TYPE_VIDEO:
            db.session.add(MediaVideo(id=res.id, title=title, duration=0.0))

        created_items.append(res)

    db.session.commit()

    # 5. 格式化返回
    result_data = [format_upload_response(item) for item in created_items]
    return jsonify({"code": 200, "msg": "success", "data": result_data}), 200
