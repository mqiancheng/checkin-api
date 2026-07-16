from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db import SessionLocal
from app.models import Setting, Task
from app.executor import execute_task

_scheduler = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        tz = "Asia/Shanghai"
        with SessionLocal() as db:
            s = db.query(Setting).first()
            if s and s.timezone:
                tz = s.timezone
        _scheduler = BackgroundScheduler(timezone=tz)
    return _scheduler


def build_trigger(task: Task) -> CronTrigger:
    jitter = task.random_delay or 0
    if task.schedule_type == "cron" and task.cron_expr:
        return CronTrigger.from_crontab(task.cron_expr, jitter=jitter)
    return CronTrigger(hour=task.hour, minute=task.minute, jitter=jitter)


def add_job(task: Task):
    get_scheduler().add_job(
        execute_task,
        trigger=build_trigger(task),
        id=f"task_{task.id}",
        replace_existing=True,
        args=[task.id],
    )


def remove_job(task_id: int):
    try:
        get_scheduler().remove_job(f"task_{task_id}")
    except Exception:
        pass


def reload_one(task_id: int):
    with SessionLocal() as db:
        t = db.get(Task, task_id)
    if t and t.enabled:
        add_job(t)
    else:
        remove_job(task_id)


def reload_all():
    sched = get_scheduler()
    for job in sched.get_jobs():
        job.remove()
    with SessionLocal() as db:
        for t in db.query(Task).filter(Task.enabled == True).all():  # noqa: E712
            try:
                add_job(t)
            except Exception:
                pass


def start():
    reload_all()
    get_scheduler().start()


def shutdown():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
