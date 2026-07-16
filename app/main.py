import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import scheduler
from app.db import SessionLocal, init_db
from app.executor import execute_task
from app.models import RunLog, Setting, Task
from app.raw_parser import parse_raw


# ---------- 请求体模型 ----------
class TaskIn(BaseModel):
    name: str = "未命名任务"
    enabled: bool = True
    method: str = "POST"
    url: str = ""
    headers: dict = {}
    cookies: dict = {}
    params: dict = {}
    body: str = ""
    body_type: str = "json"
    raw_text: str = ""
    executor_type: str = "http"   # http | browser
    cf_bypass: str = "auto"       # auto | on | off
    schedule_type: str = "daily"
    hour: int = 9
    minute: int = 0
    cron_expr: str = ""
    random_delay: int = 0
    logic: str = "AND"
    conditions: list = []
    fields: list = []
    response_type: str = "json"


class ParseIn(BaseModel):
    raw: str


class SettingsIn(BaseModel):
    wecom_enabled: bool = False
    wecom_webhook: str = ""
    timezone: str = "Asia/Shanghai"
    bypass_url: str = ""
    chrome_path: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="签到助手", lifespan=lifespan)


# ---------- RAW 解析 ----------
@app.post("/api/parse")
def api_parse(body: ParseIn):
    return parse_raw(body.raw)


# ---------- 任务 CRUD ----------
@app.get("/api/tasks")
def list_tasks():
    with SessionLocal() as db:
        tasks = db.query(Task).order_by(Task.id.desc()).all()
        return [_task_out(t) for t in tasks]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int):
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "任务不存在")
        return _task_out(t)


@app.post("/api/tasks")
def create_task(body: TaskIn):
    with SessionLocal() as db:
        t = Task(**_task_kwargs(body))
        db.add(t)
        db.commit()
        db.refresh(t)
        scheduler.reload_one(t.id)
        return _task_out(t)


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskIn):
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "任务不存在")
        for k, v in _task_kwargs(body).items():
            setattr(t, k, v)
        db.commit()
        scheduler.reload_one(t.id)
        return _task_out(t)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            raise HTTPException(404, "任务不存在")
        db.delete(t)
        db.commit()
        scheduler.remove_job(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/run")
def run_task(task_id: int):
    return execute_task(task_id, manual=True)


# ---------- 日志 ----------
@app.get("/api/logs")
def list_logs(task_id: int = 0, limit: int = 50):
    with SessionLocal() as db:
        q = db.query(RunLog)
        if task_id:
            q = q.filter(RunLog.task_id == task_id)
        logs = q.order_by(RunLog.id.desc()).limit(limit).all()
        return [
            {
                "id": l.id,
                "task_id": l.task_id,
                "task_name": l.task_name,
                "success": l.success,
                "status_code": l.status_code,
                "formatted": l.formatted,
                "error": l.error,
                "ran_at": l.ran_at.isoformat() if l.ran_at else "",
            }
            for l in logs
        ]


@app.get("/api/logs/{log_id}")
def log_detail(log_id: int):
    with SessionLocal() as db:
        l = db.get(RunLog, log_id)
        if not l:
            raise HTTPException(404, "日志不存在")
        return {
            "id": l.id,
            "task_id": l.task_id,
            "task_name": l.task_name,
            "success": l.success,
            "status_code": l.status_code,
            "formatted": l.formatted,
            "raw_response": l.raw_response,
            "error": l.error,
            "ran_at": l.ran_at.isoformat() if l.ran_at else "",
        }


# ---------- 全局设置 ----------
@app.get("/api/settings")
def get_settings():
    with SessionLocal() as db:
        s = db.query(Setting).first()
        return {
            "wecom_enabled": s.wecom_enabled,
            "wecom_webhook": s.wecom_webhook,
            "timezone": s.timezone,
            "bypass_url": s.bypass_url,
            "chrome_path": s.chrome_path,
        }


@app.put("/api/settings")
def update_settings(body: SettingsIn):
    with SessionLocal() as db:
        s = db.query(Setting).first()
        s.wecom_enabled = body.wecom_enabled
        s.wecom_webhook = body.wecom_webhook
        s.timezone = body.timezone
        s.bypass_url = body.bypass_url
        s.chrome_path = body.chrome_path
        db.commit()
        scheduler.reload_all()
    return {"ok": True}


# ---------- 工具函数 ----------
def _task_kwargs(body: TaskIn) -> dict:
    return {
        "name": body.name,
        "enabled": body.enabled,
        "method": body.method,
        "url": body.url,
        "headers": json.dumps(body.headers, ensure_ascii=False),
        "cookies": json.dumps(body.cookies, ensure_ascii=False),
        "params": json.dumps(body.params, ensure_ascii=False),
        "body": body.body,
        "body_type": body.body_type,
        "raw_text": body.raw_text,
        "executor_type": body.executor_type,
        "cf_bypass": body.cf_bypass,
        "schedule_type": body.schedule_type,
        "hour": body.hour,
        "minute": body.minute,
        "cron_expr": body.cron_expr,
        "random_delay": body.random_delay,
        "logic": body.logic,
        "conditions": json.dumps(body.conditions, ensure_ascii=False),
        "fields": json.dumps(body.fields, ensure_ascii=False),
        "response_type": body.response_type,
    }


def _task_out(t: Task) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "enabled": t.enabled,
        "method": t.method,
        "url": t.url,
        "headers": json.loads(t.headers or "{}"),
        "cookies": json.loads(t.cookies or "{}"),
        "params": json.loads(t.params or "{}"),
        "body": t.body,
        "body_type": t.body_type,
        "raw_text": t.raw_text,
        "executor_type": t.executor_type,
        "cf_bypass": t.cf_bypass,
        "schedule_type": t.schedule_type,
        "hour": t.hour,
        "minute": t.minute,
        "cron_expr": t.cron_expr,
        "random_delay": t.random_delay,
        "logic": t.logic,
        "conditions": json.loads(t.conditions or "[]"),
        "fields": json.loads(t.fields or "[]"),
        "response_type": t.response_type,
        "created_at": t.created_at.isoformat() if t.created_at else "",
        "updated_at": t.updated_at.isoformat() if t.updated_at else "",
    }


# ---------- 前端静态资源 ----------
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    @app.get("/")
    def index_fallback():
        return {
            "msg": "后端已启动，但未找到前端静态文件。请先构建前端（见 README）。API 文档见 /docs",
        }
