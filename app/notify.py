import httpx

from app.db import SessionLocal
from app.models import Setting


def _get_settings() -> Setting | None:
    with SessionLocal() as db:
        return db.query(Setting).first()


def notify(task_name: str, success: bool, content: str):
    """企业微信机器人通知（全局开关）。"""
    s = _get_settings()
    if not s or not s.wecom_enabled or not s.wecom_webhook:
        return
    title = f"✅ {task_name} 签到成功" if success else f"❌ {task_name} 签到失败"
    markdown = f"### {title}\n> {content}"
    try:
        httpx.post(
            s.wecom_webhook,
            json={"msgtype": "markdown", "markdown": {"content": markdown}},
            timeout=10,
        )
    except Exception:
        pass
