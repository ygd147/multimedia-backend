from app.plugins import PluginBase

class VideoPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "video"

    def get_blueprints(self):
        from .api import video_bp
        return [(video_bp, "/api/video")]

    def get_tasks(self):
        from app.scheduler.tasks.video_scan import video_scan
        return [
            {
                "id": "video_scan_cron",
                "func": video_scan,
                "trigger": "interval",
                "hours": 6,
                "run_on_startup": False,  # 启动时立即执行一次
            }
        ]
