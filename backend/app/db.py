import os
import sqlite3
from pathlib import Path

默认数据目录 = Path(__file__).resolve().parent.parent / "data"
默认夹具目录 = Path(__file__).resolve().parent.parent.parent / "fixtures"
库名 = "日镜账.db"

忙等毫秒 = 3000


def 数据目录() -> Path:
    return Path(os.environ.get("数据目录", 默认数据目录))


def 夹具目录() -> Path:
    return Path(os.environ.get("夹具目录", 默认夹具目录))


def 库路径() -> Path:
    return 数据目录() / 库名


def 连接() -> sqlite3.Connection:
    目录 = 数据目录()
    目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径(), timeout=忙等毫秒 / 1000)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(f"PRAGMA busy_timeout = {忙等毫秒}")
    return c
