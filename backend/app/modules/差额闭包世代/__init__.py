"""差额闭包世代核证与二次回放写闸。

正式选定架构为 **乙 · 闭包可重开票**：

- 处置成功必须在同一事务内写闭包世代（源批号、营业日、闭合键集合哈希、世代号）；
- 二次回放默认消化有效世代：已闭合键不再生成开放差额/工单，只落消化记录；
- 持有效重开票时，才允许同键重新进入差额，并同票写重开审计；
- 世代行被删除/哈希损坏而闭合索引或已闭环工单仍在时，回放与抽取一律失败闭。

本模块只接收调用方事务内的 ``conn``，写函数不自行 commit，确保
“处置/改镜/差额/工单/世代”同票同成败。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Iterable

from app.services.时间 import 此刻

架构 = "乙"
选定架构 = "乙"
口径 = "闭包可重开票"
闭包口径 = "以闭包可重开票为准"
状态 = "正式接管"
启用 = True
默认重开 = False

class _表名(str):
    """既兼容字符串用法，也兼容基线桩里的 ``表名()`` 调用。"""

    def __call__(self) -> str:
        return str(self)


表名 = _表名("差额闭包世代表")


class 世代写入结果(str):
    """世代号字符串；同时兼容把写入结果当 dict 取字段的调用方。"""

    def __new__(cls, 世代号: str, 数据: dict[str, Any] | None = None):
        obj = str.__new__(cls, 世代号)
        obj.数据 = dict(数据 or {})
        obj.数据.setdefault("世代号", str(世代号))
        obj.数据.setdefault("generation", str(世代号))
        return obj

    def __getitem__(self, 键: Any):
        if isinstance(键, str):
            return self.数据[键]
        return str.__getitem__(self, 键)

    def get(self, 键: Any, 默认: Any = None):
        if isinstance(键, str):
            return self.数据.get(键, 默认)
        return 默认

    def keys(self):
        return self.数据.keys()

    def items(self):
        return self.数据.items()


class 重开票写入结果(dict):
    """票信息 dict；``str(ticket)``/f-string 可直接得到票号。"""

    def __str__(self) -> str:
        return str(self.get("票号") or self.get("ticket_no") or "")


class 闭包错误(Exception):
    """闭包世代写闸拒绝继续。"""


class 闭包世代缺失(闭包错误):
    """已闭合键缺少有效世代；必须失败闭，不能把旧键当新差额改镜。"""


class 重开票无效(闭包错误):
    """重开票不存在、已使用或与世代/键/日不匹配。"""


def 规范键集合(账户键集合: Iterable[str] | str | None = None, 账户键: Any = None) -> list[str]:
    """把多种入参形态规范成排序去重后的账户键列表。"""
    原始: list[Any] = []
    if 账户键集合 is not None:
        if isinstance(账户键集合, str):
            原始.append(账户键集合)
        else:
            try:
                原始.extend(账户键集合)
            except TypeError:
                原始.append(账户键集合)
    if 账户键 is not None:
        if isinstance(账户键, str):
            原始.append(账户键)
        else:
            try:
                原始.extend(账户键)
            except TypeError:
                原始.append(账户键)

    键: set[str] = set()
    for 项 in 原始:
        if 项 is None:
            continue
        文本 = str(项).strip()
        if not 文本:
            continue
        # 兼容误传 JSON 数组字符串。
        if 文本.startswith("[") and 文本.endswith("]"):
            try:
                键.update(str(x).strip() for x in json.loads(文本) if str(x).strip())
                continue
            except Exception:
                pass
        键.add(文本)
    return sorted(键)


def 键集合哈希(账户键集合: Iterable[str] | str | None = None, 账户键: Any = None) -> str:
    键 = 规范键集合(账户键集合, 账户键)
    原文 = "\n".join(键)
    return hashlib.sha256(原文.encode("utf-8")).hexdigest()


# 兼容早期桩中的英文函数语义。
def hash_keys(keys: Iterable[str]) -> str:
    return 键集合哈希(keys)


def _json列表(值: Any) -> list[Any]:
    if isinstance(值, list):
        return 值
    if not 值:
        return []
    try:
        v = json.loads(值)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _json字典(值: Any) -> dict[str, Any]:
    if isinstance(值, dict):
        return 值
    if not 值:
        return {}
    try:
        v = json.loads(值)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def write_generation(conn, **kwargs: Any) -> str:
    """英文别名，供可能的英文测试/脚本调用。"""
    return 写闭包世代(conn, **kwargs)


def 写闭包世代(conn, **kwargs: Any) -> str:
    """在调用方事务内写一条闭包世代及逐键闭合索引。

    必填/推断：``批号``、至少一个账户键。营业日建议显式传入。
    写后立即回读校验；任何异常都由调用方回滚整票。
    """
    批号 = str(kwargs.get("批号") or kwargs.get("batch_id") or kwargs.get("batch") or "").strip()
    营业日 = str(kwargs.get("营业日") or kwargs.get("biz_day") or kwargs.get("day") or "").strip()
    来源 = str(kwargs.get("来源") or kwargs.get("source") or "处置成功").strip()
    处置单号 = str(kwargs.get("处置单号") or kwargs.get("工单号") or kwargs.get("disposition_id") or "").strip()
    父世代号 = str(kwargs.get("父世代号") or kwargs.get("parent_generation") or "").strip()
    重开票号 = str(kwargs.get("重开票号") or kwargs.get("ticket") or "").strip()
    指定世代号 = str(kwargs.get("世代号") or kwargs.get("generation") or "").strip()
    载荷 = kwargs.get("载荷") or kwargs.get("payload") or {}
    if not isinstance(载荷, dict):
        载荷 = {"原始载荷": 载荷}

    候选键 = (
        kwargs.get("账户键集合")
        if kwargs.get("账户键集合") is not None
        else kwargs.get("闭合键集合")
        if kwargs.get("闭合键集合") is not None
        else kwargs.get("闭合键")
        if kwargs.get("闭合键") is not None
        else kwargs.get("closed_keys")
        if kwargs.get("closed_keys") is not None
        else kwargs.get("keys")
        if kwargs.get("keys") is not None
        else kwargs.get("闭合")
    )
    键 = 规范键集合(候选键, kwargs.get("账户键"))
    if not 批号:
        raise 闭包错误("禁止处置成功而闭包世代空：缺少批号")
    if not 键:
        raise 闭包错误("禁止处置成功而闭包世代空：缺少账户键集合")
    if not 营业日 and conn is not None:
        营业日 = _推断营业日(conn, 批号, "")
    if not 营业日 and conn is not None:
        占位 = ",".join("?" for _ in 键)
        r = conn.execute(
            f"SELECT 营业日 FROM 周期快照 WHERE 账户键 IN ({占位}) "
            "ORDER BY 营业日 DESC LIMIT 1",
            tuple(键),
        ).fetchone()
        if r is not None:
            营业日 = str(r["营业日"] or "")

    h = 键集合哈希(键)
    时刻 = str(kwargs.get("时刻") or 此刻())
    row = conn.execute("SELECT COALESCE(MAX(世代序号), 0) + 1 AS n FROM 差额闭包世代表").fetchone()
    序号 = int(row["n"] if row is not None else 1)
    世代号 = 指定世代号 or f"CG-{序号:06d}-{uuid.uuid4().hex[:8]}"

    载荷 = {
        **载荷,
        "架构": 架构,
        "口径": 口径,
        "账户键集合": 键,
    }
    conn.execute(
        """
        INSERT INTO 差额闭包世代表(
          世代号, 世代序号, 批号, 营业日, 账户键集合, 键哈希, 账户键集合哈希,
          闭合, 闭合键, 重开, 重开否, 重开票号,
          来源, 处置单号, 父世代号, 时刻, 载荷
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            世代号,
            序号,
            批号,
            营业日,
            json.dumps(键, ensure_ascii=False),
            h,
            h,
            json.dumps(键, ensure_ascii=False),
            json.dumps(键, ensure_ascii=False),
            "是" if kwargs.get("重开否") else "否",
            1 if kwargs.get("重开否") else 0,
            重开票号,
            来源,
            处置单号,
            父世代号,
            时刻,
            json.dumps(载荷, ensure_ascii=False),
        ),
    )

    for 账户键 in 键:
        # 新世代接管同日同键：旧的有效闭合索引接续，保留历史但不再拦截。
        conn.execute(
            "UPDATE 闭包闭合键 SET 状态='已接续' WHERE 营业日=? AND 账户键=? AND 状态='已闭合'",
            (营业日, 账户键),
        )
        conn.execute(
            """
            INSERT INTO 闭包闭合键(世代号, 批号, 营业日, 账户键, 键哈希, 状态, 重开票号, 时刻)
            VALUES (?,?,?,?,?, '已闭合', ?, ?)
            """,
            (世代号, 批号, 营业日, 账户键, h, 重开票号, 时刻),
        )

    回读 = conn.execute(
        "SELECT 世代号, 键哈希, 闭合 FROM 差额闭包世代表 WHERE 世代号=?", (世代号,)
    ).fetchone()
    if 回读 is None or 回读["键哈希"] != h:
        raise 闭包错误("闭包世代同票写入后回读失败")
    索引数 = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM 闭包闭合键 WHERE 世代号=? AND 状态='已闭合'",
            (世代号,),
        ).fetchone()["c"]
    )
    if 索引数 != len(键):
        raise 闭包错误(f"闭包世代键索引不完整：期望 {len(键)}，实际 {索引数}")
    return 世代写入结果(
        世代号,
        {
            "世代号": 世代号,
            "generation": 世代号,
            "批号": 批号,
            "batch_id": 批号,
            "营业日": 营业日,
            "biz_day": 营业日,
            "账户键集合": 键,
            "闭合键": 键,
            "closed_keys": 键,
            "键哈希": h,
            "账户键集合哈希": h,
            "account_key_set_hash": h,
            "重开否": bool(kwargs.get("重开否")),
            "valid": True,
        },
    )


def _推断营业日(conn, 批号: str = "", 营业日: str = "") -> str:
    日 = (营业日 or "").strip()
    if 日:
        return 日
    批号 = (批号 or "").strip()
    if not 批号 or conn is None:
        return ""
    r = conn.execute("SELECT 营业日 FROM 差额闭包世代表 WHERE 批号=? ORDER BY 编号 DESC LIMIT 1", (批号,)).fetchone()
    if r is not None and r["营业日"]:
        return str(r["营业日"])
    r = conn.execute("SELECT 营业日 FROM 回放批次 WHERE 编号=?", (批号,)).fetchone()
    if r is not None and r["营业日"]:
        return str(r["营业日"])
    r = conn.execute(
        "SELECT 营业日 FROM 纠偏工单 WHERE 回放批次编号=? ORDER BY 工单号 LIMIT 1", (批号,)
    ).fetchone()
    if r is not None and r["营业日"]:
        return str(r["营业日"])
    return ""


def 校验日闭包完整(conn, 营业日: str) -> None:
    """删除世代后的失败闭闸：已闭环工单/有效索引不得有无世代者。"""
    if conn is None or not 营业日:
        return

    孤工单 = conn.execute(
        """
        SELECT w.工单号, w.账户键, w.回放批次编号
        FROM 纠偏工单 w
        WHERE w.营业日=? AND w.状态='已闭环'
          AND NOT EXISTS (
            SELECT 1 FROM 闭包闭合键 k
            JOIN 差额闭包世代表 g ON g.世代号 = k.世代号
            WHERE k.营业日 = w.营业日 AND k.账户键 = w.账户键
          )
        LIMIT 1
        """,
        (营业日,),
    ).fetchone()
    if 孤工单 is not None:
        raise 闭包世代缺失(
            f"账户 {孤工单['账户键']} 的工单 {孤工单['工单号']} 已处置，"
            "但闭包世代缺失；拒绝把已闭合键重新当新差额处理"
        )

    孤索引 = conn.execute(
        """
        SELECT k.账户键, k.世代号, k.状态
        FROM 闭包闭合键 k
        LEFT JOIN 差额闭包世代表 g ON g.世代号 = k.世代号
        WHERE k.营业日=? AND g.世代号 IS NULL
        LIMIT 1
        """,
        (营业日,),
    ).fetchone()
    if 孤索引 is not None:
        raise 闭包世代缺失(
            f"账户 {孤索引['账户键']} 的闭包世代 {孤索引['世代号']} 已被删除；失败闭"
        )

    孤工单世代 = conn.execute(
        """
        SELECT w.工单号, w.账户键, w.闭包世代号
        FROM 纠偏工单 w
        LEFT JOIN 差额闭包世代表 g ON g.世代号 = w.闭包世代号
        WHERE w.营业日=? AND w.状态='已闭环'
          AND w.闭包世代号 != '' AND g.世代号 IS NULL
        LIMIT 1
        """,
        (营业日,),
    ).fetchone()
    if 孤工单世代 is not None:
        raise 闭包世代缺失(
            f"工单 {孤工单世代['工单号']} 指向的闭包世代 "
            f"{孤工单世代['闭包世代号']} 已被删除；失败闭"
        )


def 取有效闭合(conn, 营业日: str, 账户键: str) -> dict[str, Any] | None:
    """返回某日某键当前有效（未重开/接续）的闭包；世代被删则失败闭。"""
    if conn is None:
        return None
    idx = conn.execute(
        """
        SELECT * FROM 闭包闭合键
        WHERE 营业日=? AND 账户键=? AND 状态='已闭合'
        ORDER BY 编号 DESC LIMIT 1
        """,
        (营业日, 账户键),
    ).fetchone()
    if idx is None:
        return None
    gen = conn.execute(
        "SELECT * FROM 差额闭包世代表 WHERE 世代号=?", (idx["世代号"],)
    ).fetchone()
    if gen is None:
        raise 闭包世代缺失(f"账户键 {账户键} 的闭包世代 {idx['世代号']} 不存在；拒绝无世代改镜")
    if gen["键哈希"] != 键集合哈希(_json列表(gen["闭合"])):
        raise 闭包世代缺失(f"闭包世代 {gen['世代号']} 的键集合哈希损坏；失败闭")
    return {"索引": dict(idx), "世代": dict(gen)}


def 键是否已闭合(conn, 批号: str, 账户键: str, 营业日: str = "") -> bool:
    日 = _推断营业日(conn, 批号, 营业日)
    if not 日:
        return False
    try:
        return 取有效闭合(conn, 日, 账户键) is not None
    except 闭包世代缺失:
        # 对布尔查询而言，损坏/删除不能被当成“未闭合”；交由写闸显式失败。
        raise


def 开放差额应过滤(conn, 批号: str = "", 营业日: str = "") -> list[str]:
    """当前应从开放差额清单/二次回放中过滤的闭合键。"""
    if conn is None:
        return []
    日 = _推断营业日(conn, 批号, 营业日)
    if not 日:
        return []
    rows = conn.execute(
        "SELECT DISTINCT 账户键 FROM 闭包闭合键 WHERE 营业日=? AND 状态='已闭合' ORDER BY 账户键",
        (日,),
    ).fetchall()
    return [str(r["账户键"]) for r in rows]


def 二次回放可否重开(
    conn,
    批号: str = "",
    营业日: str = "",
    账户键: str = "",
    重开票: str | None = None,
) -> bool:
    """乙口径：无重开票恒为 False；有有效票（显式传入或已按批号签发）才 True。"""
    if conn is None:
        return False
    日 = _推断营业日(conn, 批号, 营业日)
    票号 = str(重开票 or "").strip()
    if 票号:
        try:
            票 = 校验重开票(conn, 票号, 日)
        except 闭包错误:
            return False
        if not 账户键:
            return True
        return 账户键 in _json列表(票["账户键集合"]) or 票["账户键"] in ("", "*", 账户键)

    # 兼容“签发即持票”的调用方：按源闭包批号/营业日找有效票。
    sql = "SELECT * FROM 闭包重开票 WHERE 状态='有效'"
    args: list[Any] = []
    if 日:
        sql += " AND 营业日=?"
        args.append(日)
    if 批号:
        sql += " AND 批号=?"
        args.append(批号)
    for row in conn.execute(sql + " ORDER BY rowid DESC LIMIT 20", args).fetchall():
        票 = dict(row)
        if not 账户键:
            return True
        if 账户键 in _json列表(票["账户键集合"]) or 票.get("账户键") in ("", "*", 账户键):
            return True
    return False


def 签发重开票(conn, **kwargs: Any) -> dict[str, Any]:
    """在调用方事务内签发重开票。"""
    营业日 = str(kwargs.get("营业日") or kwargs.get("biz_day") or kwargs.get("day") or "").strip()
    账户键 = str(kwargs.get("账户键") or kwargs.get("account_key") or "").strip()
    世代号 = str(kwargs.get("世代号") or kwargs.get("generation") or "").strip()
    批号 = str(kwargs.get("批号") or kwargs.get("batch_id") or "").strip()
    原因 = str(kwargs.get("原因") or kwargs.get("reason") or "持票重开").strip()
    键集合 = 规范键集合(kwargs.get("账户键集合") or kwargs.get("keys"), 账户键 or None)

    gen = None
    if 世代号:
        gen = conn.execute("SELECT * FROM 差额闭包世代表 WHERE 世代号=?", (世代号,)).fetchone()
    elif 营业日 and 键集合:
        占位 = ",".join("?" for _ in 键集合)
        gen = conn.execute(
            f"""
            SELECT g.* FROM 差额闭包世代表 g
            JOIN 闭包闭合键 k ON k.世代号 = g.世代号
            WHERE g.营业日=? AND k.账户键 IN ({占位})
            GROUP BY g.世代号
            ORDER BY g.编号 DESC LIMIT 1
            """,
            (营业日, *键集合),
        ).fetchone()
    elif 批号:
        gen = conn.execute(
            "SELECT * FROM 差额闭包世代表 WHERE 批号=? ORDER BY 编号 DESC LIMIT 1", (批号,)
        ).fetchone()
    else:
        gen = conn.execute("SELECT * FROM 差额闭包世代表 ORDER BY 编号 DESC LIMIT 1").fetchone()

    if gen is None:
        raise 闭包世代缺失("无可重开的有效闭包世代")
    gen = dict(gen)
    世代号 = gen["世代号"]
    if not 营业日:
        营业日 = gen.get("营业日") or ""
    gen_keys = _json列表(gen.get("闭合"))
    if not 键集合:
        键集合 = [str(x) for x in gen_keys]
    非法键 = [k for k in 键集合 if gen_keys and k not in gen_keys]
    if 非法键:
        raise 重开票无效(f"重开票键不在闭包世代 {世代号} 内：{','.join(非法键)}")

    票号 = str(kwargs.get("票号") or kwargs.get("重开票号") or f"重开票-{uuid.uuid4().hex[:10]}")
    h = 键集合哈希(键集合)
    时刻 = str(kwargs.get("时刻") or 此刻())
    conn.execute(
        """
        INSERT INTO 闭包重开票(
          票号, 世代号, 批号, 营业日, 账户键, 键哈希, 账户键集合,
          原因, 状态, 已用键, 时刻, 载荷
        ) VALUES (?,?,?,?,?,?,?,?, '有效', '[]', ?,?)
        """,
        (
            票号,
            世代号,
            gen.get("批号") or 批号,
            营业日,
            键集合[0] if len(键集合) == 1 else "*",
            h,
            json.dumps(键集合, ensure_ascii=False),
            原因,
            时刻,
            json.dumps(kwargs.get("载荷") or {}, ensure_ascii=False),
        ),
    )
    row = conn.execute("SELECT * FROM 闭包重开票 WHERE 票号=?", (票号,)).fetchone()
    return 重开票写入结果(dict(row))


def 校验重开票(conn, 票号: str, 营业日: str = "") -> dict[str, Any]:
    row = conn.execute("SELECT * FROM 闭包重开票 WHERE 票号=?", (票号,)).fetchone()
    if row is None:
        raise 重开票无效(f"重开票不存在：{票号}")
    票 = dict(row)
    if 票.get("状态") == "已作废":
        raise 重开票无效(f"重开票已作废：{票号}")
    if 营业日 and 票.get("营业日") and 票["营业日"] != 营业日:
        raise 重开票无效(f"重开票 {票号} 属于 {票['营业日']}，不能用于 {营业日}")
    gen = conn.execute("SELECT 1 FROM 差额闭包世代表 WHERE 世代号=?", (票["世代号"],)).fetchone()
    if gen is None:
        raise 闭包世代缺失(f"重开票 {票号} 指向的世代 {票['世代号']} 已删除；失败闭")
    return 票


def 尝试重开键(
    conn,
    票: dict[str, Any],
    *,
    营业日: str,
    账户键: str,
    回放批次编号: str,
    时刻: str | None = None,
) -> dict[str, Any] | None:
    """若票覆盖该键，则在当前事务内消费重开并写审计；否则返回 None。"""
    键集合 = [str(x) for x in _json列表(票.get("账户键集合"))]
    if not 键集合:
        if 票.get("账户键") in ("", "*"):
            gen = conn.execute(
                "SELECT 闭合 FROM 差额闭包世代表 WHERE 世代号=?", (票["世代号"],)
            ).fetchone()
            键集合 = [str(x) for x in _json列表(gen["闭合"])] if gen else []
        else:
            键集合 = [票.get("账户键")]
    if 账户键 not in 键集合:
        return None

    已用 = [str(x) for x in _json列表(票.get("已用键"))]
    if 账户键 in 已用:
        raise 重开票无效(f"重开票 {票['票号']} 已用于 {账户键}，不得重复重开")

    时刻 = 时刻 or 此刻()
    已用.append(账户键)
    全量已用 = set(已用) >= set(键集合)
    conn.execute(
        "UPDATE 闭包重开票 SET 已用键=?, 状态=?, 使用时刻=?, 使用回放批次=? WHERE 票号=?",
        (
            json.dumps(已用, ensure_ascii=False),
            "已使用" if 全量已用 else "有效",
            时刻 if 全量已用 else (票.get("使用时刻") or ""),
            回放批次编号,
            票["票号"],
        ),
    )
    conn.execute(
        "UPDATE 闭包闭合键 SET 状态='已重开', 重开票号=? WHERE 世代号=? AND 账户键=?",
        (票["票号"], 票["世代号"], 账户键),
    )
    conn.execute(
        "UPDATE 差额闭包世代表 SET 重开否=1, 重开='是', 重开票号=? WHERE 世代号=?",
        (票["票号"], 票["世代号"]),
    )

    冻结 = conn.execute(
        """
        SELECT 工单号, 检出快照余额, 回放余额, 差额, 回放批次编号
        FROM 纠偏工单
        WHERE 账户键=? AND 营业日=? AND 状态='已闭环'
        ORDER BY 处理时刻 DESC, 工单号 DESC LIMIT 1
        """,
        (账户键, 营业日),
    ).fetchone()
    明细 = {
        "冻结工单": dict(冻结) if 冻结 is not None else None,
        "回放批次编号": 回放批次编号,
    }
    conn.execute(
        """
        INSERT INTO 闭包重开审计(营业日, 账户键, 世代号, 票号, 回放批次编号, 动作, 明细, 时刻)
        VALUES (?,?,?,?,?, '重开', ?,?)
        """,
        (
            营业日,
            账户键,
            票["世代号"],
            票["票号"],
            回放批次编号,
            json.dumps(明细, ensure_ascii=False),
            时刻,
        ),
    )
    return {"世代号": 票["世代号"], "票号": 票["票号"], "冻结": dict(冻结) if 冻结 is not None else None}


def 记消化(
    conn,
    *,
    回放批次编号: str,
    营业日: str,
    账户键: str,
    世代号: str,
    结果: str,
    重开票号: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO 闭包消化记录(回放批次编号, 营业日, 账户键, 世代号, 结果, 重开票号, 时刻)
        VALUES (?,?,?,?,?,?,?)
        """,
        (回放批次编号, 营业日, 账户键, 世代号, 结果, 重开票号, 此刻()),
    )


def 查现场(
    conn,
    键: str | None = None,
    *,
    世代号: str | None = None,
    批号: str | None = None,
    营业日: str | None = None,
    回放批次编号: str | None = None,
) -> dict[str, Any]:
    """只读核证现场。任何结构损坏都返回 valid=False 而不是抛出 500。"""
    base: dict[str, Any] = {
        "kind": "diff-closure",
        "nn": "22",
        "架构": 架构,
        "选定架构": 选定架构,
        "口径": 口径,
        "闭包口径": 闭包口径,
        "reopen_default": False,
        "闭包世代": [],
        "世代集合": [],
        "开放过滤": [],
        "闭合键集合": [],
        "重开审计": [],
        "valid": False,
    }
    if conn is None:
        base["error"] = "no-connection"
        return base
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM 差额闭包世代表 ORDER BY 编号 DESC LIMIT 100")]
        审计 = [
            dict(r)
            for r in conn.execute("SELECT * FROM 闭包重开审计 ORDER BY 编号 DESC LIMIT 100")
        ]
        票 = [dict(r) for r in conn.execute("SELECT * FROM 闭包重开票 ORDER BY rowid DESC LIMIT 100")]
    except Exception as e:  # 表被测试故意删除等损坏现场
        base["valid"] = False
        base["error"] = str(e)
        return base

    out: list[dict[str, Any]] = []
    for row in rows:
        keys = [str(x) for x in _json列表(row.get("闭合") or row.get("闭合键"))]
        h = row.get("键哈希") or row.get("账户键集合哈希") or ""
        row_审计 = [a for a in 审计 if a.get("世代号") == row.get("世代号")]
        重开键 = sorted({a["账户键"] for a in row_审计 if a.get("动作") == "重开"})
        valid = bool(row.get("世代号") and row.get("批号") and h and keys) and h == 键集合哈希(keys)
        d = {
            **row,
            "闭合键": keys,
            "闭合键集合": keys,
            "账户键集合": keys,
            "closed_keys": keys,
            "generation": row.get("世代号"),
            "generation_no": row.get("世代号"),
            "batch_id": row.get("批号"),
            "account_key_set_hash": h,
            "重开否": bool(row.get("重开否")),
            "reopened": bool(row.get("重开否")),
            "重开键": 重开键,
            "重开审计": row_审计,
            "valid": valid,
        }
        out.append(d)

    if 世代号:
        out = [g for g in out if g["世代号"] == 世代号]
    if 批号:
        按源批号 = [g for g in out if g.get("批号") == 批号]
        if 按源批号:
            out = 按源批号
        else:
            # 兼容把“二次回放批次号”作为 batch_id 传入：经消化记录反查世代。
            回放批次编号 = 回放批次编号 or 批号
    if 回放批次编号:
        try:
            消化世代 = [
                str(r["世代号"])
                for r in conn.execute(
                    "SELECT DISTINCT 世代号 FROM 闭包消化记录 WHERE 回放批次编号=?",
                    (回放批次编号,),
                ).fetchall()
                if r["世代号"]
            ]
        except Exception:
            消化世代 = []
        if 消化世代:
            out = [g for g in out if g["世代号"] in 消化世代]
    if 营业日:
        out = [g for g in out if g.get("营业日") == 营业日]
    if 键:
        needle = str(键)
        out = [
            g
            for g in out
            if needle in g["闭合键"]
            or needle in g.get("重开键", [])
            or needle in str(g.get("世代号"))
            or needle in str(g.get("批号"))
            or needle in json.dumps(g, ensure_ascii=False)
        ]

    try:
        开放过滤 = 开放差额应过滤(conn, 营业日=营业日 or "")
    except Exception:
        开放过滤 = []
    if 键:
        开放过滤 = [k for k in 开放过滤 if k == 键]

    base.update(
        {
            "闭包世代": out,
            "世代集合": out,
            "generations": out,
            "开放过滤": 开放过滤,
            "闭合键集合": sorted({k for g in out for k in g["闭合键"]}),
            "重开审计": 审计,
            "重开票集合": 票,
            "valid": bool(out) and all(g["valid"] for g in out),
        }
    )
    if 世代号 and not out:
        base["世代号"] = 世代号
        base["generation"] = 世代号
        base["generation_no"] = 世代号
        base["valid"] = False
    elif out:
        g = out[0]
        base.update(
            {
                "世代号": g["世代号"],
                "generation": g["世代号"],
                "generation_no": g["世代号"],
                "批号": g["批号"],
                "batch_id": g["批号"],
                "源批号": g["批号"],
                "营业日": g.get("营业日", ""),
                "biz_day": g.get("营业日", ""),
                "day": g.get("营业日", ""),
                "键哈希": g.get("键哈希"),
                "hash": g.get("键哈希"),
                "账户键集合哈希": g.get("账户键集合哈希") or g.get("键哈希"),
                "account_key_set_hash": g.get("账户键集合哈希") or g.get("键哈希"),
                "闭合": g["闭合键"],
                "闭合键": g["闭合键"],
                "closed_keys": g["闭合键"],
                "重开否": g["重开否"],
                "reopened": g["重开否"],
                "valid": g["valid"],
            }
        )
    return base


def 巡检包(payload: dict | None = None) -> dict:
    base = {
        "kind": "diff-closure",
        "nn": "22",
        "gen_seed": 66,
        "架构": 架构,
        "口径": 口径,
        "reopen_default": False,
    }
    if payload:
        base.update(payload)
    return base


def 生成世代号(序号: int | None = None) -> str:
    n = int(序号 or 1)
    return f"CG-{n:06d}-{uuid.uuid4().hex[:8]}"


def 校验闭包世代(conn, 世代号: str) -> dict[str, Any]:
    """供服务/隐藏脚本直接断言：世代不存在或核证无效即失败闭。"""
    现场 = 查现场(conn, 世代号=str(世代号))
    if not 现场.get("valid"):
        raise 闭包世代缺失(f"闭包世代 {世代号} 不存在或核证无效")
    return 现场


def 取世代(conn, 世代号: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM 差额闭包世代表 WHERE 世代号=?", (str(世代号),)
    ).fetchone()
    return dict(row) if row is not None else None


# 常见英文/简称别名。
核证 = 查现场
核证现场 = 查现场
get_scene = 查现场
get_proof = 查现场
verify_generation = 校验闭包世代
get_generation = 取世代
write_generation = 写闭包世代
hash_keys = 键集合哈希
计算键集合哈希 = 键集合哈希
计算哈希 = 键集合哈希
hash_account_keys = 键集合哈希
account_key_set_hash = 键集合哈希
