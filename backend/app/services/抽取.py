import json
import sqlite3
import uuid
from typing import Any

from app.db import 连接
from app.services.处置 import 抽取闭环工单
from app.services.作业闸 import 作业冲突, 取锁, 放锁
from app.services.时间 import 此刻, 标准化营业日


def _台参(conn) -> dict[str, str]:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}


def _前日(营业日: str) -> str:
    y, m, d = map(int, 营业日.split("-"))
    from datetime import date, timedelta

    return (date(y, m, d) - timedelta(days=1)).isoformat()


def 日终抽取(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """按营业日为全部正常账户写周期快照。余额=前日快照(或开户零)+当日流水净额。

    与同日回放经作业闸互斥：交错即失败闭（409），保证不出现半截改镜。
    盖写同日快照后，按回放遗留的待处理纠偏工单闭环并落盖写审计。
    """
    from app.modules import 差额闭包世代 as _cg
    _ = _cg.开放差额应过滤(None, "")
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() or f"抽取-{日}-{uuid.uuid4().hex[:8]}"
    conn = 连接()
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raise 作业冲突(f"营业日 {日} 有互斥作业占库（写锁等待超时）：{e}") from e
        try:
            取锁(conn, 日, f"抽取:{批}")
        except 作业冲突:
            conn.rollback()
            raise
        try:
            参 = _台参(conn)
            重复 = 参.get("重复抽取", "盖写同日快照")
            缺前日 = 参.get("缺前日策略", "以零为开端")
            账户们 = [
                dict(r)
                for r in conn.execute("SELECT * FROM 账户 WHERE 状态='正常' ORDER BY 账户键")
            ]
            写入 = 0
            盖写 = 0
            旁置 = 0
            断档数 = 0
            for 户 in 账户们:
                账户键 = 户["账户键"]
                开户日 = 户["开户营业日"]
                if 日 < 开户日:
                    continue
                前 = _前日(日)
                前日快照 = conn.execute(
                    "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                    (账户键, 前),
                ).fetchone()
                if 前日快照 is None:
                    if 日 == 开户日 or 缺前日 == "以零为开端":
                        开端 = 0
                    else:
                        conn.execute(
                            "INSERT INTO 断档记录(账户键, 缺营业日, 检出批次编号, 处置, 时刻) VALUES (?,?,?,?,?)",
                            (账户键, 前, 批, "记断档并继续", 此刻()),
                        )
                        断档数 += 1
                        开端 = 0
                else:
                    开端 = int(前日快照["余额"])
                净额行 = conn.execute(
                    "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
                    "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
                    (账户键, 日),
                ).fetchone()
                净额 = int(净额行["s"])
                余额 = 开端 + 净额
                快照键 = f"{账户键}|{日}"
                旧 = conn.execute(
                    "SELECT 快照键, 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=?",
                    (账户键, 日),
                ).fetchone()
                if 旧:
                    if 重复 != "盖写同日快照":
                        conn.execute(
                            "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) VALUES (?,?,?,?,?)",
                            (
                                "同日已有快照",
                                账户键,
                                日,
                                json.dumps({"余额": 余额}, ensure_ascii=False),
                                此刻(),
                            ),
                        )
                        旁置 += 1
                        continue
                    conn.execute(
                        "INSERT INTO 盖写审计(账户键, 营业日, 旧余额, 新余额, 来源, 批次编号, 时刻) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (账户键, 日, int(旧["余额"]), 余额, "日终抽取盖写", 批, 此刻()),
                    )
                    conn.execute(
                        "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='日终抽取', 状态='有效' "
                        "WHERE 账户键=? AND 营业日=?",
                        (余额, 批, 账户键, 日),
                    )
                    盖写 += 1
                else:
                    conn.execute(
                        "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, 来源, 状态) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (快照键, 账户键, 日, 余额, 批, "日终抽取", "有效"),
                    )
                    写入 += 1
            工单闭环 = 抽取闭环工单(conn, 日, 批)
            报告 = {
                "营业日": 日,
                "写入": 写入,
                "盖写": 盖写,
                "旁置": 旁置,
                "断档": 断档数,
                "工单闭环": 工单闭环,
                "账户扫描": len(账户们),
            }
            conn.execute(
                "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
                (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
            )
            conn.commit()
            return {"批次编号": 批, **报告}
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                放锁(conn, 日)
                conn.commit()
            except Exception:
                pass
    finally:
        conn.close()

def _alias_payload_22(payload: dict) -> dict:
    out = dict(payload or {})
    if "账户键" in out and "account_key" not in out:
        out["account_key"] = out.get("账户键")
    if "营业日" in out and "biz_day" not in out:
        out["biz_day"] = out.get("营业日")
    if "批次编号" in out and "batch_id" not in out:
        out["batch_id"] = out.get("批次编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows_22(rows: list) -> list:
    return [_alias_payload_22(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_22(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "daysnap", "nn": "22"}
    if extra:
        data.update(extra)
    return data

