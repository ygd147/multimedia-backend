"""
Novel 插件入口
"""

from app.plugins import PluginBase
from .api import novel_bp

class NovelPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "novel"

    def get_blueprints(self):
        return [(novel_bp, "/api/novel")]

    def get_tasks(self):
        """向调度器注册每天12点的定时任务"""
        def _daily_scan_job():
            # 延迟导入，确保在 Flask 上下文内
            from flask import current_app
            from app.extensions import switcher
            from .scanner import NovelScanner

            if not switcher.is_enabled("novel"):
                return
            if switcher.is_running("novel"):
                return
                
            base_path = current_app.config.get("NOVEL_BASE_PATH")
            switcher.set_running("novel", True)
            try:
                NovelScanner(base_path).scan()
            except Exception as e:
                current_app.logger.error(f"[小说]定时扫描失败: {e}")
            finally:
                switcher.set_running("novel", False)

        return [
            {
                "id": "novel_daily_scan",
                "func": _daily_scan_job,
                "trigger": "cron",
                "hour": "12",
                "minute": "0",
            }
        ]
