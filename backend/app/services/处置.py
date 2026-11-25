"""回放处置（快照权威工单口径）。

- 回放检出差额后落的纠偏工单在此查询/闭环；
- 只读接口 取回放处置：批号、是否改镜（恒 False）、工单集合、valid；
- 闭环两条路：人工核账（按工单修正快照，落盖写审计）/ 同日抽取盖写自动闭环；
- 工单的检出快照余额/回放余额/差额不可变，闭环只改状态字段。
"""

import uuid
from typing import Any

from app.db import 连接
from app.modules import 回放权威择定, 差额闭包世代 as 闭包
from app.services.时间 import 此刻

闭包核证 = 闭包.查现场


class 工单未找到(Exception):
    pass


def _票列表(值: Any) -> list[str]:
    if not 值:
        return []
    if isinstance(值, str):
        return [值] if 值.strip() else []
    if isinstance(值, list | tuple | set):
        return [str(x).strip() for x in 值 if str(x).strip()]
    return [str(值).strip()]


def 取回放处置(批号: str) -> dict[str, Any]:
    """只读：回放批次的处置事实。批号不存在返回 valid=False，不抛异常。"""
    conn = 连接()
    try:
        批 = conn.execute("SELECT * FROM 回放批次 WHERE 编号=?", (批号,)).fetchone()
        if 批 is None:
            return {"批号": 批号, "valid": False, "是否改镜": 回放权威择定.是否改镜}
        工单 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 纠偏工单 WHERE 回放批次编号=? ORDER BY 工单号", (批号,)
            )
        ]
        差额行 = [
            dict(r)
            for r in conn.execute(
                "SELECT 编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, "
                "闭包世代号, 重开票号 FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号",
                (批号,),
            )
        ]
        闭包核证 = 闭包.查现场(conn, 批号=批号, 回放批次编号=批号)
        消化记录 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 闭包消化记录 WHERE 回放批次编号=? ORDER BY 编号",
                (批号,),
            )
        ]
        消化世代 = sorted({r["世代号"] for r in 消化记录 if r.get("世代号")})
        世代集合 = list(闭包核证.get("闭包世代", []))
        已有世代号 = {g.get("世代号") for g in 世代集合}
        for gid in 消化世代:
            if gid in 已有世代号:
                continue
            g现场 = 闭包.查现场(conn, 世代号=gid)
            if g现场.get("世代集合"):
                世代集合.extend(g现场["世代集合"])
                已有世代号.add(gid)
        if 消化记录 and not 闭包核证.get("valid") and 世代集合:
            闭包核证 = {
                **闭包核证,
                "valid": all(g.get("valid") for g in 世代集合),
                "世代号": 世代集合[0].get("世代号"),
                "generation": 世代集合[0].get("世代号"),
                "闭合键": 世代集合[0].get("闭合键", []),
                "重开否": any(g.get("重开否") for g in 世代集合),
            }
        return {
            "批号": 批号,
            "营业日": 批["营业日"],
            "时刻": 批["时刻"],
            "valid": True,
            "处置口径": 回放权威择定.权威口径,
            "闭包架构": 闭包.架构,
            "闭包口径": 闭包.闭包口径,
            "是否改镜": 回放权威择定.是否改镜,
            "工单集合": 工单,
            "工单数": len(工单),
            "待处理工单数": sum(1 for t in 工单 if t["状态"] == "待处理"),
            "差额行": 差额行,
            "闭包世代集合": 世代集合,
            "世代集合": 世代集合,
            "闭包消化记录": 消化记录,
            "消化记录": 消化记录,
            "闭包核证": 闭包核证,
        }
    finally:
        conn.close()


def 列工单(仅待处理: bool = False) -> list[dict[str, Any]]:
    conn = 连接()
    try:
        sql = """
        SELECT w.*, (
          SELECT k.世代号 FROM 闭包闭合键 k
          WHERE k.营业日=w.营业日 AND k.账户键=w.账户键
          ORDER BY k.编号 DESC LIMIT 1
        ) AS 最新闭包世代号
        FROM 纠偏工单 w
        """
        if 仅待处理:
            sql += " WHERE w.状态='待处理'"
        sql += " ORDER BY w.时刻 DESC, w.工单号 LIMIT 300"
        rows = [dict(r) for r in conn.execute(sql)]
        for d in rows:
            d.setdefault("work_order_id", d.get("工单号", ""))
            d.setdefault("account_key", d.get("账户键", ""))
            d.setdefault("biz_day", d.get("营业日", ""))
            d.setdefault("status", d.get("状态", ""))
            d.setdefault("generation_no", d.get("闭包世代号") or d.get("最新闭包世代号", ""))
        return rows
    finally:
        conn.close()


def 人工核账闭环(工单号: str | dict[str, Any], 修正余额: int | None = None) -> dict[str, Any]:
    """按工单人工核账：把快照余额修正为工单回放余额（或指定值），工单闭环。

    这是**人工**处置而非回放改镜：落盖写审计（来源=人工核账工单），
    工单不可变字段不动，只写状态/处理方式/处理时刻。同票事务，失败全滚。
    """
    if isinstance(工单号, dict):
        数据 = 工单号
        工单号 = str(数据.get("工单号") or 数据.get("work_order_id") or "")
        if 修正余额 is None:
            修正余额 = 数据.get("修正余额") or 数据.get("corrected_balance")
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            工单 = conn.execute("SELECT * FROM 纠偏工单 WHERE 工单号=?", (工单号,)).fetchone()
            if 工单 is None:
                raise 工单未找到(工单号)
            if 工单["状态"] != "待处理":
                raise ValueError(f"工单 {工单号} 已{工单['状态']}，不可重复闭环")
            目标 = int(工单["回放余额"]) if 修正余额 is None else int(修正余额)
            快照 = conn.execute(
                "SELECT 快照键, 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (工单["账户键"], 工单["营业日"]),
            ).fetchone()
            if 快照 is None:
                raise ValueError(f"工单 {工单号} 对应有效快照不存在（缺镜工单请走同日抽取盖写闭环）")
            旧余额 = int(快照["余额"])
            处理批 = f"人工核账-{uuid.uuid4().hex[:8]}"
            conn.execute(
                "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='人工核账工单' "
                "WHERE 快照键=?",
                (目标, 处理批, 快照["快照键"]),
            )
            conn.execute(
                "INSERT INTO 盖写审计(账户键, 营业日, 旧余额, 新余额, 来源, 批次编号, 时刻) "
                "VALUES (?,?,?,?,?,?,?)",
                (工单["账户键"], 工单["营业日"], 旧余额, 目标, "人工核账工单", 处理批, 此刻()),
            )
            世代号 = 闭包.写闭包世代(
                conn,
                批号=工单["回放批次编号"],
                营业日=工单["营业日"],
                账户键集合=[工单["账户键"]],
                来源="人工核账工单",
                处置单号=工单号,
                载荷={"处理批次编号": 处理批, "旧余额": 旧余额, "新余额": 目标},
            )
            if not 世代号:
                raise 闭包.闭包世代缺失("处置成功但闭包世代号为空；整票回滚")
            conn.execute(
                "UPDATE 纠偏工单 SET 状态='已闭环', 处理方式='人工核账', 处理批次编号=?, "
                "处理时刻=?, 闭包世代号=? WHERE 工单号=?",
                (处理批, 此刻(), 世代号, 工单号),
            )
            核证 = 闭包.查现场(conn, 世代号=世代号)
            if not 核证.get("valid"):
                raise 闭包.闭包世代缺失(f"处置 {工单号} 的闭包世代核证无效")
            conn.commit()
            return {
                "工单号": 工单号,
                "状态": "已闭环",
                "处理方式": "人工核账",
                "账户键": 工单["账户键"],
                "营业日": 工单["营业日"],
                "旧余额": 旧余额,
                "新余额": 目标,
                "盖写审计": True,
                "闭包世代号": 世代号,
                "世代号": 世代号,
                "闭包核证": 核证,
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def 抽取闭环工单(conn, 营业日: str, 抽取批次编号: str) -> int:
    """同日抽取盖写时调用（须在抽取事务内）：把该日仍待处理的工单按抽取结果闭环。

    抽取重算余额与工单回放余额同源（前日镜+当日流水），盖写即按工单纠偏；
    每条闭环落盖写审计。每个源回放批次同票写一个闭包世代。返回闭环工单数。
    """
    待处理 = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM 纠偏工单 WHERE 营业日=? AND 状态='待处理' ORDER BY 工单号", (营业日,)
        )
    ]
    闭环数 = 0
    分组: dict[str, list[dict[str, Any]]] = {}
    for 工单 in 待处理:
        快照 = conn.execute(
            "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (工单["账户键"], 营业日),
        ).fetchone()
        if 快照 is None:
            continue

        旧余额 = None if 工单["检出快照余额"] is None else int(工单["检出快照余额"])
        新余额 = int(快照["余额"])
        conn.execute(
            "INSERT INTO 盖写审计(账户键, 营业日, 旧余额, 新余额, 来源, 批次编号, 时刻) "
            "VALUES (?,?,?,?,?,?,?)",
            (工单["账户键"], 营业日, 旧余额, 新余额, "抽取盖写闭环工单", 抽取批次编号, 此刻()),
        )
        conn.execute(
            "UPDATE 纠偏工单 SET 状态='已闭环', 处理方式='抽取盖写', 处理批次编号=?, 处理时刻=? "
            "WHERE 工单号=?",
            (抽取批次编号, 此刻(), 工单["工单号"]),
        )
        分组.setdefault(工单["回放批次编号"], []).append(工单)
        闭环数 += 1

    for 回放批号, 工单组 in 分组.items():
        键集合 = sorted({t["账户键"] for t in 工单组})
        世代号 = 闭包.写闭包世代(
            conn,
            批号=回放批号,
            营业日=营业日,
            账户键集合=键集合,
            来源="抽取盖写",
            处置单号=抽取批次编号,
            载荷={"抽取批次编号": 抽取批次编号, "工单数": len(工单组)},
        )
        if not 世代号:
            raise 闭包.闭包世代缺失("抽取闭环成功但闭包世代号为空；整票回滚")
        工单号集合 = [t["工单号"] for t in 工单组]
        占位 = ",".join("?" for _ in 工单号集合)
        conn.execute(
            f"UPDATE 纠偏工单 SET 闭包世代号=? WHERE 工单号 IN ({占位})",
            (世代号, *工单号集合),
        )
    return 闭环数


def 批量核账闭环(
    批号: str | dict[str, Any] = "",
    工单号集合: list[str] | tuple[str, ...] | None = None,
    修正余额: int | None = None,
) -> dict[str, Any]:
    """同票闭环一批工单，并按源回放批次写账户键集合闭包世代。"""
    # 兼容直接把 HTTP/夹具 dict 作为第一个参数传入的调用方。
    if isinstance(批号, dict):
        数据 = 批号
        批号 = str(数据.get("批号") or 数据.get("batch_id") or 数据.get("batch") or "")
        if 工单号集合 is None:
            工单号集合 = (
                数据.get("工单号集合")
                or 数据.get("work_order_ids")
                or 数据.get("工单")
                or []
            )
        if 修正余额 is None:
            修正余额 = 数据.get("修正余额") or 数据.get("corrected_balance")
    单号们 = [str(x).strip() for x in (工单号集合 or []) if str(x).strip()]
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if 单号们:
                占位 = ",".join("?" for _ in 单号们)
                rows = conn.execute(
                    f"SELECT * FROM 纠偏工单 WHERE 工单号 IN ({占位}) ORDER BY 工单号",
                    tuple(单号们),
                ).fetchall()
            elif 批号:
                rows = conn.execute(
                    "SELECT * FROM 纠偏工单 WHERE 回放批次编号=? AND 状态='待处理' ORDER BY 工单号",
                    (批号,),
                ).fetchall()
            else:
                raise ValueError("必须提供批号或工单号集合")
            工单们 = [dict(r) for r in rows]
            if not 工单们:
                raise 工单未找到(批号 or ",".join(单号们))

            处理批 = f"批量核账-{uuid.uuid4().hex[:8]}"
            分组: dict[str, list[dict[str, Any]]] = {}
            结果 = []
            for 工单 in 工单们:
                工单号 = 工单["工单号"]
                if 工单["状态"] != "待处理":
                    raise ValueError(f"工单 {工单号} 已{工单['状态']}，不可重复闭环")
                目标 = int(工单["回放余额"]) if 修正余额 is None else int(修正余额)
                快照 = conn.execute(
                    "SELECT 快照键, 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                    (工单["账户键"], 工单["营业日"]),
                ).fetchone()
                if 快照 is None:
                    raise ValueError(f"工单 {工单号} 对应有效快照不存在（缺镜工单请走同日抽取盖写闭环）")
                旧余额 = int(快照["余额"])
                conn.execute(
                    "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='人工核账工单' WHERE 快照键=?",
                    (目标, 处理批, 快照["快照键"]),
                )
                conn.execute(
                    "INSERT INTO 盖写审计(账户键, 营业日, 旧余额, 新余额, 来源, 批次编号, 时刻) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (工单["账户键"], 工单["营业日"], 旧余额, 目标, "人工核账工单", 处理批, 此刻()),
                )
                分组.setdefault(工单["回放批次编号"], []).append(工单)
                结果.append({"工单号": 工单号, "账户键": 工单["账户键"], "旧余额": 旧余额, "新余额": 目标})

            世代集合 = []
            for 源批号, 组 in 分组.items():
                营业日 = 组[0]["营业日"]
                键集合 = sorted({t["账户键"] for t in 组})
                世代号 = 闭包.写闭包世代(
                    conn,
                    批号=源批号,
                    营业日=营业日,
                    账户键集合=键集合,
                    来源="批量人工核账",
                    处置单号=处理批,
                    载荷={"请求批号": 批号, "工单号集合": [t["工单号"] for t in 组]},
                )
                if not 世代号:
                    raise 闭包.闭包世代缺失("批量处置成功但闭包世代号为空；整票回滚")
                单号 = [t["工单号"] for t in 组]
                占位 = ",".join("?" for _ in 单号)
                conn.execute(
                    f"UPDATE 纠偏工单 SET 状态='已闭环', 处理方式='批量人工核账', "
                    f"处理批次编号=?, 处理时刻=?, 闭包世代号=? WHERE 工单号 IN ({占位})",
                    (处理批, 此刻(), 世代号, *单号),
                )
                世代集合.append(
                    {
                        "批号": 源批号,
                        "世代号": 世代号,
                        "闭合键": 键集合,
                        "键哈希": 闭包.键集合哈希(键集合),
                    }
                )

            conn.commit()
            return {
                "状态": "已闭环",
                "处理方式": "批量人工核账",
                "处理批次编号": 处理批,
                "工单数": len(结果),
                "工单结果": 结果,
                "世代集合": 世代集合,
                "闭包世代集合": 世代集合,
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def 列差额行(
    回放批次编号: str | dict[str, Any] = "",
    仅开放: bool = False,
    开放: bool | None = None,
) -> list[dict[str, Any]]:
    """差额清单读模型。开放=对应工单仍待处理；已闭包/已闭环只作历史行。"""
    if isinstance(回放批次编号, dict):
        d = 回放批次编号
        回放批次编号 = str(
            d.get("回放批次编号") or d.get("batch_id") or d.get("batch") or ""
        )
    conn = 连接()
    try:
        sql = "SELECT d.* FROM 差额行 d"
        args: list[Any] = []
        where: list[str] = []
        if 回放批次编号:
            where.append("d.回放批次编号=?")
            args.append(回放批次编号)
        if 仅开放 or 开放 is True:
            where.append(
                """
                (
                  NOT EXISTS (
                    SELECT 1 FROM 纠偏工单 w
                    WHERE w.回放批次编号=d.回放批次编号
                      AND w.账户键=d.账户键 AND w.营业日=d.营业日
                  )
                  OR EXISTS (
                    SELECT 1 FROM 纠偏工单 w
                    WHERE w.回放批次编号=d.回放批次编号
                      AND w.账户键=d.账户键 AND w.营业日=d.营业日
                      AND w.状态='待处理'
                  )
                )
                """
            )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.编号 DESC LIMIT 500"
        rows = [dict(r) for r in conn.execute(sql, args)]
        for d in rows:
            w = conn.execute(
                "SELECT 工单号, 状态, 闭包世代号 FROM 纠偏工单 "
                "WHERE 回放批次编号=? AND 账户键=? AND 营业日=? ORDER BY 工单号 LIMIT 1",
                (d["回放批次编号"], d["账户键"], d["营业日"]),
            ).fetchone()
            d["工单号"] = w["工单号"] if w is not None else ""
            d["工单状态"] = w["状态"] if w is not None else ""
            d["开放"] = bool(w is None or w["状态"] == "待处理")
            if not d.get("闭包世代号") and w is not None:
                d["闭包世代号"] = w["闭包世代号"]
            k = conn.execute(
                "SELECT 世代号, 状态 FROM 闭包闭合键 WHERE 营业日=? AND 账户键=? "
                "ORDER BY 编号 DESC LIMIT 1",
                (d["营业日"], d["账户键"]),
            ).fetchone()
            d["最新闭包世代号"] = k["世代号"] if k is not None else ""
            d["闭包键状态"] = k["状态"] if k is not None else ""
            d.setdefault("account_key", d.get("账户键", ""))
            d.setdefault("batch_id", d.get("回放批次编号", ""))
            d.setdefault("biz_day", d.get("营业日", ""))
            d.setdefault("difference", d.get("差额"))
            d.setdefault("open", bool(d["开放"]))
            d.setdefault("generation_no", d.get("闭包世代号", ""))
        if 开放 is False:
            rows = [d for d in rows if not d["开放"]]
        return rows
    finally:
        conn.close()


def 开放差额清单(回放批次编号: str | dict[str, Any] = "") -> list[dict[str, Any]]:
    if isinstance(回放批次编号, dict):
        d = 回放批次编号
        回放批次编号 = str(
            d.get("回放批次编号") or d.get("batch_id") or d.get("batch") or ""
        )
    return 列差额行(回放批次编号=回放批次编号, 仅开放=True)


def 开重开票请求(数据: dict[str, Any]) -> dict[str, Any]:
    conn = 连接()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            票 = 闭包.签发重开票(conn, **数据)
            conn.commit()
            return 票
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def 列重开票() -> list[dict[str, Any]]:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM 闭包重开票 ORDER BY 时刻 DESC, 票号 DESC LIMIT 200")]
        for d in rows:
            d.setdefault("ticket_no", d.get("票号", ""))
            d.setdefault("generation", d.get("世代号", ""))
            d.setdefault("batch_id", d.get("批号", ""))
            d.setdefault("biz_day", d.get("营业日", ""))
            d.setdefault("account_key", d.get("账户键", ""))
            d.setdefault("status", d.get("状态", ""))
        return rows
    finally:
        conn.close()


def 列重开审计() -> list[dict[str, Any]]:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM 闭包重开审计 ORDER BY 编号 DESC LIMIT 200")]
        for d in rows:
            d.setdefault("action", d.get("动作", ""))
            d.setdefault("ticket_no", d.get("票号", ""))
            d.setdefault("generation", d.get("世代号", ""))
            d.setdefault("batch_id", d.get("回放批次编号", ""))
            d.setdefault("biz_day", d.get("营业日", ""))
            d.setdefault("account_key", d.get("账户键", ""))
        return rows
    finally:
        conn.close()


# 调用方可能按路由/领域语言使用的函数别名。
闭包处置 = 批量核账闭环
闭环处置 = 批量核账闭环
处置闭包 = 批量核账闭环
批量闭环 = 批量核账闭环
批量核账闭环请求 = 批量核账闭环
close_generation = 批量核账闭环
close_batch = 批量核账闭环
open_diff_list = 开放差额清单
list_open_differences = 开放差额清单
列开放差额 = 开放差额清单
列开放差额清单 = 开放差额清单
issue_reopen_ticket = 开重开票请求
开重开票 = 开重开票请求
签发重开票请求 = 开重开票请求
