"""回放处置（快照权威工单口径）。

- 回放检出差额后落的纠偏工单在此查询/闭环；
- 只读接口 取回放处置：批号、是否改镜（恒 False）、工单集合、valid；
- 闭环两条路：人工核账（按工单修正快照，落盖写审计）/ 同日抽取盖写自动闭环；
- 工单的检出快照余额/回放余额/差额不可变，闭环只改状态字段。
"""

import uuid
from typing import Any

from app.db import 连接
from app.modules import 回放权威择定
from app.services.时间 import 此刻


class 工单未找到(Exception):
    pass


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
                "SELECT 编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因 "
                "FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号",
                (批号,),
            )
        ]
        return {
            "批号": 批号,
            "营业日": 批["营业日"],
            "时刻": 批["时刻"],
            "valid": True,
            "处置口径": 回放权威择定.权威口径,
            
            "是否改镜": 回放权威择定.是否改镜,
            "工单集合": 工单,
            "工单数": len(工单),
            "待处理工单数": sum(1 for t in 工单 if t["状态"] == "待处理"),
            "差额行": 差额行,
        }
    finally:
        conn.close()


def 列工单(仅待处理: bool = False) -> list[dict[str, Any]]:
    conn = 连接()
    try:
        sql = "SELECT * FROM 纠偏工单"
        if 仅待处理:
            sql += " WHERE 状态='待处理'"
        sql += " ORDER BY 时刻 DESC, 工单号 LIMIT 300"
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def 人工核账闭环(工单号: str, 修正余额: int | None = None) -> dict[str, Any]:
    """按工单人工核账：把快照余额修正为工单回放余额（或指定值），工单闭环。

    这是**人工**处置而非回放改镜：落盖写审计（来源=人工核账工单），
    工单不可变字段不动，只写状态/处理方式/处理时刻。同票事务，失败全滚。
    """
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
            conn.execute(
                "UPDATE 纠偏工单 SET 状态='已闭环', 处理方式='人工核账', 处理批次编号=?, 处理时刻=? "
                "WHERE 工单号=?",
                (处理批, 此刻(), 工单号),
            )
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
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def 抽取闭环工单(conn, 营业日: str, 抽取批次编号: str) -> int:
    """同日抽取盖写时调用（须在抽取事务内）：把该日仍待处理的工单按抽取结果闭环。

    抽取重算余额与工单回放余额同源（前日镜+当日流水），盖写即按工单纠偏；
    每条闭环落盖写审计。返回闭环工单数。
    """
    待处理 = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM 纠偏工单 WHERE 营业日=? AND 状态='待处理'", (营业日,)
        )
    ]
    闭环数 = 0
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
        闭环数 += 1
    return 闭环数
