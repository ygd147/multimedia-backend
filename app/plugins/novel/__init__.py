from app.plugins import PluginBase

class NovelPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "novel"

    def get_blueprints(self):
        from .api import novel_bp
        return [(novel_bp, "/api/novel")]

    def get_tasks(self):
        from app.scheduler.tasks.novel_scan import novel_scan
        return [
            {
                "id": "novel_daily_scan",
                "func": novel_scan,
                "trigger": "cron",
                "hour": 12,
                "minute": 0,
                "run_on_startup": False,  # 按需开启
            }
        ]
