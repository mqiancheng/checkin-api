from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from app.db import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, default="未命名任务")
    enabled = Column(Boolean, default=True)

    method = Column(String(10), default="POST")
    url = Column(Text, nullable=False, default="")
    headers = Column(Text, default="{}")   # JSON dict
    cookies = Column(Text, default="{}")   # JSON dict
    params = Column(Text, default="{}")    # JSON dict (query string)
    body = Column(Text, default="")
    body_type = Column(String(20), default="json")  # json | form | raw | none
    raw_text = Column(Text, default="")    # 原始 RAW 备份，可重新解析

    # 执行方式
    executor_type = Column(String(10), default="http", nullable=False)  # http | browser
    cf_bypass = Column(String(8), default="auto", nullable=False)       # auto | on | off

    # 调度
    schedule_type = Column(String(10), default="daily")  # daily | cron
    hour = Column(Integer, default=9)
    minute = Column(Integer, default=0)
    cron_expr = Column(String(50), default="")
    random_delay = Column(Integer, default=0)  # 最大随机延时（秒）

    # 判定与展示
    logic = Column(String(5), default="AND")           # AND | OR
    conditions = Column(Text, default="[]")            # JSON list
    fields = Column(Text, default="[]")                # JSON list
    response_type = Column(String(10), default="json") # json | text

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class RunLog(Base):
    __tablename__ = "run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False, default=0)
    task_name = Column(String(200), default="")
    success = Column(Boolean, default=False)
    status_code = Column(Integer, default=0)
    formatted = Column(Text, default="")     # 最终结果摘要（字段映射/原始响应），日志列表显示用
    raw_response = Column(Text, default="")  # 完整原始响应（调试用）
    error = Column(Text, default="")
    ran_at = Column(DateTime, default=datetime.now)          # 开始执行时间（日志创建时刻）
    finished_at = Column(DateTime, default=None)             # 执行完毕时间
    process_log = Column(Text, default="")   # 执行过程步骤（▶ 开头的行），详情弹窗展示用


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    wecom_enabled = Column(Boolean, default=False)
    wecom_webhook = Column(Text, default="")
    timezone = Column(String(50), default="Asia/Shanghai")
    bypass_url = Column(Text, default="")  # NAS Cloudflare bypass 服务地址（留空则禁用 cf_bypass）
    chrome_path = Column(Text, default="")  # chrome 模式使用的浏览器可执行文件路径（本地测试用）


class BypassCache(Base):
    """按域名缓存从 NAS bypass 服务获取的 cf_clearance，避免每次调用都消耗资源。"""

    __tablename__ = "bypass_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), unique=True, nullable=False)  # 域名（netloc），如 www.vikacg.cc
    cf_clearance = Column(Text, nullable=False)
    user_agent = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
