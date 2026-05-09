"""Comic 插件注册"""

from app.plugins import PluginBase


class ComicPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "comic"

    def get_blueprints(self):
        from .api import media_bp
        # ⭐ 直接挂载到 /api/media，完美对齐前端 baseURL + /media
        return [(media_bp, "/api/media")]

    def get_tasks(self):
        from app.scheduler.tasks.comic_scan import comic_scan
        return [
            {
                "id": "comic_scan",
                "func": comic_scan,
                "trigger": "interval",
                "hours": _scan_interval(),
                "run_on_startup": False,
            }
        ]


def _scan_interval() -> int:
    from app.config import Config
    return Config.SCAN_INTERVAL_HOURS
