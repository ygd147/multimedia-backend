import os
import time
import random
from flask import Blueprint, request, jsonify, current_app
from app.extensions import db
from app.models import Media, MediaType
from app.config import Config

upload_bp = Blueprint('upload', __name__, url_prefix='/api')

ALLOWED_EXTENSIONS = {
    'media': {'.jpg', '.jpeg', '.png', '.webp', '.zip'},
    'novel': {'.txt', '.epub'},
    'video': {'.mp4', '.mkv', '.avi', '.mov'}
}

TYPE_MAP = {
    'media': MediaType.IMAGE,
    'novel': MediaType.NOVEL,
    'video': MediaType.VIDEO
}

def allowed_file(filename, type_key):
    return '.' in filename and \
           os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS.get(type_key, set())

def safe_unicode_filename(filename: str) -> str:
    filename = filename.replace('\\', '/').split('/')[-1]
    filename = filename.strip(' .')
    return filename if filename else f"upload_{int(time.time())}"

@upload_bp.route('/<type_key>/upload', methods=['POST'])
def upload_files(type_key):
    current_app.logger.info(f"收到上传请求: type_key={type_key}")

    if type_key not in TYPE_MAP:
        return jsonify({"code": 400, "msg": "Invalid media type", "data": None}), 400

    target_media_type = TYPE_MAP[type_key]
    
    files = request.files.getlist('files')
    parent_id_str = request.form.get('parent_id')
    
    current_app.logger.info(f"解析参数: parent_id_str={parent_id_str}, 文件数量={len(files)}")

    if not files or files[0].filename == '':
        return jsonify({"code": 400, "msg": "No files selected", "data": None}), 400

    if target_media_type == MediaType.IMAGE: 
        root_abs_path = Config.COMIC_BASE_PATH
    elif target_media_type == MediaType.NOVEL: 
        root_abs_path = Config.NOVEL_BASE_PATH
    elif target_media_type == MediaType.VIDEO: 
        root_abs_path = Config.VIDEO_BASE_PATH
    else:
        return jsonify({"code": 400, "msg": "Invalid media type config", "data": None}), 400

    abs_save_dir = root_abs_path
    
    if parent_id_str and parent_id_str.lower() not in ['null', 'undefined', '']:
        try:
            parent_id = int(parent_id_str)
        except ValueError:
            current_app.logger.warning(f"目录ID格式错误: {parent_id_str}")
            return jsonify({"code": 400, "msg": f"Invalid parent_id: {parent_id_str}", "data": None}), 400

        parent_res = db.session.get(Media, parent_id)
        if not parent_res:
            current_app.logger.warning(f"目录不存在: ID={parent_id}")
            return jsonify({"code": 404, "msg": "Target directory not found", "data": None}), 404
            
        if parent_res.is_dir != 1:
            current_app.logger.warning(f"目标不是目录: ID={parent_id}, is_dir={parent_res.is_dir}")
            return jsonify({"code": 400, "msg": "Target is not a directory", "data": None}), 400
            
        # 🚨 核心修复：兼容目录 media_type 为 0 的情况
        # 如果目录类型是 0 (通用目录)，允许任何类型上传；
        # 如果目录类型不是 0，则必须和上传文件类型完全匹配
        if parent_res.media_type != 0 and parent_res.media_type != target_media_type:
            current_app.logger.warning(f"类型不匹配: 目录类型={parent_res.media_type}, 上传类型={target_media_type}")
            return jsonify({"code": 400, "msg": "Type mismatch with parent directory", "data": None}), 400

        # 拼接绝对路径
        abs_save_dir = os.path.join(root_abs_path, parent_res.relative_path)
        current_app.logger.info(f"子目录拼接结果: {abs_save_dir}")

    # 处理文件存盘
    saved_files = []
    
    for file in files:
        original_filename = file.filename
        
        if not allowed_file(original_filename, type_key):
            current_app.logger.warning(f"文件类型不支持: {original_filename}")
            return jsonify({"code": 400, "msg": f"Unsupported file type: {original_filename}", "data": None}), 400

        safe_name = safe_unicode_filename(original_filename)
        
        if not safe_name or safe_name.startswith('.'):
            ext = os.path.splitext(original_filename)[1].lower()
            safe_name = f"{int(time.time())}_{random.randint(100, 999)}{ext}"

        os.makedirs(abs_save_dir, exist_ok=True)
        
        abs_path = os.path.join(abs_save_dir, safe_name)
        
        if os.path.exists(abs_path):
            name, ext = os.path.splitext(safe_name)
            safe_name = f"{name}_{int(time.time())}{ext}"
            abs_path = os.path.join(abs_save_dir, safe_name)

        file.save(abs_path)
        saved_files.append(safe_name)
        current_app.logger.info(f"文件保存成功: {abs_path}")

    return jsonify({
        "code": 200, 
        "msg": "Upload success, waiting for scan to import", 
        "data": {"saved_files": saved_files}
    }), 200
