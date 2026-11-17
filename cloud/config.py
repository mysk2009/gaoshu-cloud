from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


DEFAULT_DB_URL = "sqlite:///cloud/data/cloud_test.db"


@dataclass(frozen=True)
class Settings:
    db_url: str
    admin_token: str
    teacher_password: str
    secret_key: str

    @classmethod
    def from_env(cls) -> "Settings":
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
            db_url=os.getenv("CLOUD_DB_URL", DEFAULT_DB_URL),
            admin_token=os.getenv("CLOUD_ADMIN_TOKEN", ""),
            teacher_password=os.getenv("CLOUD_TEACHER_PASSWORD", ""),
            secret_key=secret_key,
        )
