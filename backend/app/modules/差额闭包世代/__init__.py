状态 = "半截"


def 键是否已闭合(conn, 批号: str, 账户键: str) -> bool:
    return False


def 二次回放可否重开(conn, 批号: str) -> bool:
    return True


def 写闭包世代(conn, **kwargs) -> str:
    return ""


def 开放差额应过滤(conn, 批号: str) -> list:
    return []

def 表名() -> str:
    return "差额闭包世代表"


def 查现场(conn, 键: str | None = None) -> dict:
    rows = []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT 世代号, 批号, 键哈希, 闭合, 重开 FROM 差额闭包世代表 ORDER BY 编号 DESC LIMIT 40"
        )]
    except Exception:
        rows = []
    if 键:
        rows = [r for r in rows if 键 in str(r.values())]
    return {"闭包世代": rows, "开放过滤": [], "nn": "22", "valid": False}


def 巡检包(payload: dict | None = None) -> dict:
    base = {"kind": "diff-closure", "nn": "22", "gen_seed": 66, "reopen_default": True}
    if payload:
        base.update(payload)
    return base
