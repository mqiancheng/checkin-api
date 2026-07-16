import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 数据文件默认放在项目根目录的 data/ 下，可用环境变量 DB_PATH 覆盖
DEFAULT_DB = os.path.join(os.path.dirname(BASE_DIR), "data", "app.db")
DB_PATH = os.environ.get("DB_PATH", DEFAULT_DB)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def _migrate_columns():
    """为已存在的旧表补充新增列（SQLAlchemy create_all 不会 ALTER 已有表）。"""
    insp = inspect(engine)

    task_cols = {c["name"] for c in insp.get_columns("tasks")}
    task_adds = {
        "executor_type": "VARCHAR(10) NOT NULL DEFAULT 'http'",
        "cf_bypass": "VARCHAR(8) NOT NULL DEFAULT 'auto'",
    }
    for col, ddl in task_adds.items():
        if col not in task_cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}"))

    runlog_cols = {c["name"] for c in insp.get_columns("run_logs")}
    runlog_adds = {
        "process_log": "TEXT DEFAULT ''",
        "finished_at": "DATETIME",
    }
    for col, ddl in runlog_adds.items():
        if col not in runlog_cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE run_logs ADD COLUMN {col} {ddl}"))

    setting_cols = {c["name"] for c in insp.get_columns("settings")}
    if "bypass_url" not in setting_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE settings ADD COLUMN bypass_url TEXT NOT NULL DEFAULT ''"))
    if "chrome_path" not in setting_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE settings ADD COLUMN chrome_path TEXT NOT NULL DEFAULT ''"))


def init_db():
    import app.models  # noqa: F401  确保模型注册
    Base.metadata.create_all(engine)
    _migrate_columns()
    # 保证存在一条全局设置
    from app.models import Setting
    with SessionLocal() as db:
        if db.query(Setting).first() is None:
            db.add(Setting())
            db.commit()
