# app/scheduler/__init__.py
"""
定时任务调度器
"""

import logging

from app.extensions import scheduler, store_app

logger = logging.getLogger(__name__)


def init_scheduler(app):
    store_app(app)

    from app.plugins import PluginManager
    all_tasks = PluginManager.collect_tasks()

    for task_def in all_tasks:
        task_id = task_def["id"]
        func = task_def["func"]
        trigger = task_def.get("trigger", "interval")
        run_on_startup = task_def.get("run_on_startup", False)
        plugin_name = task_def.get("_plugin", "unknown")

        trigger_args = {
            k: v for k, v in task_def.items()
            if k not in ("id", "func", "trigger", "run_on_startup", "_plugin")
        }

        if trigger == "interval" and trigger_args:
            valid_args = {k: v for k, v in trigger_args.items() if v}
            if valid_args:
                scheduler.add_job(
                    func,
                    trigger="interval",
                    id=task_id,
                    replace_existing=True,
                    **valid_args,
                )
                logger.info(
                    "⏰ 定时任务已注册: [%s] %s → 每 %s",
                    plugin_name, task_id, _format_interval(valid_args),
                )

        if run_on_startup:
            scheduler.add_job(
                func,
                trigger="date",
                id=f"{task_id}_startup",
                replace_existing=True,
            )
            logger.info("🚀 启动任务已注册: [%s] %s", plugin_name, task_id)

    if not scheduler.running:
        scheduler.start()
        logger.info("✅ 调度器已启动")


def _format_interval(args: dict) -> str:
    parts = []
    if args.get("hours"):
        parts.append(f"{args['hours']}小时")
    if args.get("minutes"):
        parts.append(f"{args['minutes']}分钟")
    return " ".join(parts) if parts else "未知间隔"
