from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit


DEFAULT_DB_URL = "sqlite:///cloud/data/cloud_test.db"
PRODUCTION_DB_URL_ERROR = (
    "生产环境缺少 CLOUD_DB_URL，拒绝以 sqlite 启动"
    "（会导致每次重新部署数据丢失）。请在 Render 的 Environment 里配置 Neon 连接串。"
)


def _database_summary(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        return f"backend=sqlite path={db_url.removeprefix('sqlite:///')}"

    parsed = urlsplit(db_url)
    if parsed.scheme in ("postgresql", "postgres"):
        host = parsed.hostname or "(unknown)"
        return f"backend=postgres host={host}"

    return f"backend={parsed.scheme or '(unknown)'} host=(unknown)"


@dataclass(frozen=True)
class Settings:
    db_url: str
    admin_token: str
    teacher_password: str
    secret_key: str

    @classmethod
    def from_env(cls) -> "Settings":
        db_url = os.getenv("CLOUD_DB_URL", "").strip()
        running_on_render = (
            "RENDER" in os.environ or bool(os.getenv("RENDER_SERVICE_ID", "").strip())
        )
        require_db_url = os.getenv("CLOUD_REQUIRE_DB_URL", "").strip() == "1"
        if not db_url and (running_on_render or require_db_url):
            raise RuntimeError(PRODUCTION_DB_URL_ERROR)
        if not db_url:
            db_url = DEFAULT_DB_URL
        print(f"[cloud] {_database_summary(db_url)}", flush=True)

        secret_key = os.getenv("CLOUD_SECRET_KEY", "")
        if not secret_key:
            # 空密钥 = 谁都能离线伪造老师 token。没配就每次启动随机生成一个：
            # 安全，代价是重启后已发的 token 全部失效（12 小时有效期本来就短）。
            secret_key = secrets.token_hex(32)
            print(
                "[warn] CLOUD_SECRET_KEY 未配置，已使用本次启动的随机密钥；"
                "重启后现有老师 token 全部失效。生产环境请在环境变量里配置固定值。",
                flush=True,
            )
        return cls(
            db_url=db_url,
            admin_token=os.getenv("CLOUD_ADMIN_TOKEN", ""),
            teacher_password=os.getenv("CLOUD_TEACHER_PASSWORD", ""),
            secret_key=secret_key,
        )
