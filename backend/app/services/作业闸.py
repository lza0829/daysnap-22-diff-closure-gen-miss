"""日级作业闸：回放与同日抽取盖写互斥，禁止交错半截改镜。

锁键按 (营业日) 归一：同一营业日的回放与抽取（盖写路径）同一时刻只许一个持锁。
SQLite 单写者 + 显式锁行双保险——持锁期间另一方在进事务写库前即失败闭，
不会出现差额/工单已落而镜被抽取盖掉、或镜被盖一半的半截态。
"""

from app.services.时间 import 此刻


class 作业冲突(Exception):
    """同营业日已有互斥作业在跑（HTTP 409）。"""



持锁回调 = None


def 锁键(营业日: str) -> str:
    return f"日作业|{营业日}"


def 取锁(conn, 营业日: str, 持锁者: str) -> None:
    """抢占日级作业锁；已被他人持有则抛 作业冲突。须在事务内调用。"""
    键 = 锁键(营业日)
    行 = conn.execute("SELECT 持锁者 FROM 作业锁 WHERE 锁键=?", (键,)).fetchone()
    if 行 is not None:
        raise 作业冲突(f"营业日 {营业日} 已有互斥作业在跑：{行['持锁者']}")
    conn.execute(
        "INSERT INTO 作业锁(锁键, 持锁者, 时刻) VALUES (?,?,?)",
        (键, 持锁者, 此刻()),
    )
    if 持锁回调 is not None:
        持锁回调(营业日, 持锁者)


def 放锁(conn, 营业日: str) -> None:
    conn.execute("DELETE FROM 作业锁 WHERE 锁键=?", (锁键(营业日),))
