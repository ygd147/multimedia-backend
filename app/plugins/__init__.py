"""
插件系统
══════════════════════════════════════════════════════════════
每个插件是一个 app/plugins/<name>/ 目录，必须包含：
  - __init__.py  — 定义 <Name>Plugin(PluginBase) 类
  - api.py       — Flask Blueprint（可选）
  - scanner.py   — 扫描器（可选）
  - service.py   — 业务逻辑（可选）

PluginBase 生命周期：
  1. get_blueprints()     → 注册路由
  2. get_tasks()          → 注册定时任务
  3. on_app_start(app)    → 启动后回调（如立即执行扫描）
  4. on_app_shutdown(app) → 关闭前回调
"""

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class PluginBase(ABC):
    """插件基类 — 所有插件必须继承"""

    @property
    @abstractmethod
    def name(self) -> str:
        """插件唯一标识，如 'comic', 'video', 'novel'"""
        ...

    def get_blueprints(self):
        """
        返回要注册的 Blueprint 列表。
        格式: [(Blueprint, url_prefix), ...]
        """
        return []

    def get_tasks(self):
        """
        返回定时任务定义列表。
        格式: [{
            'id': str,              # 任务唯一标识
            'func': callable,       # 任务函数（需自行处理 app_context）
            'trigger': str,         # 'interval' / 'cron'
            'run_on_startup': bool, # 是否启动时立即执行
            # trigger='interval' 时:
            'hours': int,
            'minutes': int,
            # trigger='cron' 时:
            # 'hour': str, 'minute': str, ...
        }, ...]
        """
        return []

    def on_app_start(self, app):
        """应用启动完成后回调"""
        pass

    def on_app_shutdown(self, app):
        """应用关闭前回调"""
        pass


class PluginManager:
    """
    插件管理器 — 发现、注册、初始化所有插件
    """
    _plugins: dict[str, PluginBase] = {}

    @classmethod
    def discover(cls, plugin_names: list[str]):
        """
        按名称发现并实例化插件。
        插件名 'comic' → 导入 app.plugins.comic → 取 ComicPlugin 类
        """
        for name in plugin_names:
            try:
                module = importlib.import_module(f"app.plugins.{name}")
                # 插件类名约定: comic → ComicPlugin, video → VideoPlugin
                cls_name = "".join(w.capitalize() for w in name.split("_"))
                plugin_cls = getattr(module, f"{cls_name}Plugin")
                instance = plugin_cls()
                cls._plugins[instance.name] = instance
                logger.info("✅ 插件已发现: %s", name)
            except Exception as e:
                logger.error("❌ 插件 [%s] 加载失败: %s", name, e, exc_info=True)

    @classmethod
    def register_blueprints(cls, app):
        """注册所有插件的 Blueprint"""
        for name, plugin in cls._plugins.items():
            for bp, url_prefix in plugin.get_blueprints():
                app.register_blueprint(bp, url_prefix=url_prefix)
                logger.info("   Blueprint 注册: %s → %s", bp.name, url_prefix)

    @classmethod
    def collect_tasks(cls):
        """收集所有插件的定时任务定义"""
        all_tasks = []
        for name, plugin in cls._plugins.items():
            tasks = plugin.get_tasks()
            for t in tasks:
                t.setdefault("_plugin", name)
            all_tasks.extend(tasks)
        return all_tasks

    @classmethod
    def fire_on_start(cls, app):
        """触发所有插件的 on_app_start"""
        for name, plugin in cls._plugins.items():
            try:
                plugin.on_app_start(app)
                logger.info("   插件 [%s] 启动完成", name)
            except Exception as e:
                logger.error("   插件 [%s] 启动失败: %s", name, e, exc_info=True)

    @classmethod
    def fire_on_shutdown(cls, app):
        """触发所有插件的 on_app_shutdown"""
        for name, plugin in cls._plugins.items():
            try:
                plugin.on_app_shutdown(app)
            except Exception as e:
                logger.error("   插件 [%s] 关闭异常: %s", name, e)

    @classmethod
    def get_plugin(cls, name: str) -> Optional[PluginBase]:
        return cls._plugins.get(name)

    @classmethod
    def all_plugins(cls) -> dict[str, PluginBase]:
        return dict(cls._plugins)
