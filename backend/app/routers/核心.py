import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.modules import 差额闭包世代 as 闭包模块
from app.services import 回放, 抽取, 目录, 装载, 处置
from app.services.作业闸 import 作业冲突
from app.services.处置 import 工单未找到

路由 = APIRouter(prefix="/api")


class 装载请求(BaseModel):
    批次编号: str = ""
    流水: list = Field(default_factory=list)


class 营业日请求(BaseModel):
    营业日: str
    批次编号: str = ""


class 工单闭环请求(BaseModel):
    工单号: str
    修正余额: int | None = None


class 批量闭环请求(BaseModel):
    批号: str = ""
    工单号集合: list[str] = Field(default_factory=list)
    修正余额: int | None = None


class 重开票请求(BaseModel):
    账户键: str | None = None
    营业日: str | None = None
    世代号: str | None = None
    批号: str | None = None
    原因: str = "人工申请重开"
    账户键集合: list[str] = Field(default_factory=list)


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 发生时 DESC LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 周期快照 WHERE 状态='有效'"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 300"
        rows = [dict(r) for r in conn.execute(sql, args)]
        for r in rows:
            k = conn.execute(
                "SELECT 世代号, 状态, 重开票号 FROM 闭包闭合键 "
                "WHERE 营业日=? AND 账户键=? ORDER BY 编号 DESC LIMIT 1",
                (r["营业日"], r["账户键"]),
            ).fetchone()
            r["闭包世代号"] = k["世代号"] if k is not None else ""
            r["closure_generation_no"] = r["闭包世代号"]
            r["闭包状态"] = k["状态"] if k is not None else "未闭包"
            r["closure_status"] = r["闭包状态"]
            r["重开票号"] = k["重开票号"] if k is not None else ""
            r["reopen_ticket_no"] = r["重开票号"]
        return rows
    finally:
        conn.close()


@路由.get("/开放差额")
@路由.post("/开放差额")
@路由.get("/开放差额清单")
@路由.post("/开放差额清单")
@路由.get("/差额/开放")
@路由.get("/差额/open")
@路由.get("/differences/open")
@路由.get("/open-differences")
@路由.post("/open-differences")
@路由.get("/open-diffs")
@路由.post("/open-diffs")
async def 列开放差额(request: Request):
    q = request.query_params
    批号 = q.get("回放批次编号") or q.get("batch_id") or q.get("batch") or ""
    if not 批号:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            批号 = str(
                body.get("回放批次编号")
                or body.get("batch_id")
                or body.get("batch")
                or ""
            )
    return 处置.列差额行(回放批次编号=批号, 仅开放=True)


@路由.get("/差额")
def 列差额(request: Request):
    q = request.query_params
    回放批次编号 = q.get("回放批次编号") or q.get("batch_id") or ""
    真值 = {"1", "true", "yes", "是", "开放", "仅开放"}
    全量 = (q.get("全部") or q.get("all") or q.get("history") or "").lower() in 真值
    显式关闭开放过滤 = (q.get("开放") or q.get("open") or "").lower() in {"0", "false", "no", "否"}
    仅开放 = not 全量 and not 显式关闭开放过滤 and (
        (q.get("开放") is None and q.get("仅开放") is None and q.get("开放清单") is None)
        or (q.get("开放") or "").lower() in 真值
        or (q.get("仅开放") or "").lower() in 真值
        or (q.get("开放清单") or "").lower() in 真值
        or (q.get("open") or "").lower() in 真值
        or (q.get("only_open") or "").lower() in 真值
        or q.get("状态") == "开放"
        or q.get("status") == "open"
    )
    if 全量:
        开放: bool | None = None
    elif 显式关闭开放过滤:
        开放 = False
    else:
        开放 = True
    return 处置.列差额行(
        回放批次编号=回放批次编号,
        仅开放=仅开放,
        开放=开放,
    )


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/回放处置/{batch}")
@路由.get("/处置/{batch}")
def 取回放处置接口(batch: str):
    """只读回放处置事实：批号、是否改镜（快照权威口径恒 false）、工单集合、valid。

    路径参数名须 ASCII（Starlette 路径参数不接受中文标识符），语义即批号。
    """
    return 处置.取回放处置(batch)


@路由.get("/工单")
def 列工单接口(仅待处理: bool = False):
    return 处置.列工单(仅待处理=仅待处理)


@路由.get("/盖写审计")
def 列盖写审计():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 盖写审计 ORDER BY 编号 DESC LIMIT 300")]
    finally:
        conn.close()


@路由.post("/工单闭环")
@路由.post("/工单/闭环")
@路由.post("/work-orders/close")
@路由.post("/closure/close-work-order")
def 工单闭环接口(体: dict | None = None):
    d = dict(体 or {})
    工单号 = str(d.get("工单号") or d.get("work_order_id") or d.get("work_order_no") or "")
    修正 = d.get("修正余额")
    if 修正 is None:
        修正 = d.get("corrected_balance") or d.get("balance")
    if not 工单号:
        raise HTTPException(422, "工单号不能为空")
    try:
        return 处置.人工核账闭环(工单号, 修正)
    except 工单未找到 as e:
        raise HTTPException(404, f"工单不存在: {e}") from e
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@路由.post("/批量工单闭环")
@路由.post("/工单批量闭环")
@路由.post("/闭包处置")
@路由.post("/差额闭包处置")
@路由.post("/闭包世代处置")
@路由.post("/差额闭包/处置")
@路由.post("/closure/dispose")
@路由.post("/处置")
def 批量工单闭环接口(体: dict | None = None):
    d = dict(体 or {})
    批号 = str(d.get("批号") or d.get("batch_id") or d.get("batch") or "").strip()
    单号 = d.get("工单号集合") or d.get("work_order_ids") or d.get("工单") or []
    if isinstance(单号, str):
        单号 = [单号]
    修正 = d.get("修正余额")
    try:
        return 处置.批量核账闭环(批号, list(单号) if 单号 else None, 修正)
    except 工单未找到 as e:
        raise HTTPException(404, f"工单不存在: {e}") from e
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@路由.post("/回放处置/{batch}/闭环")
def 回放批闭环接口(batch: str, 体: dict | None = None):
    d = dict(体 or {})
    单号 = d.get("工单号集合") or d.get("work_order_ids") or d.get("工单") or []
    if isinstance(单号, str):
        单号 = [单号]
    修正 = d.get("修正余额") or d.get("corrected_balance") or d.get("balance")
    try:
        return 处置.批量核账闭环(batch, list(单号) if 单号 else None, 修正)
    except 工单未找到 as e:
        raise HTTPException(404, f"批号 {batch} 无待处理工单: {e}") from e
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@路由.get("/闭包重开票")
@路由.get("/重开票")
@路由.get("/reopen-tickets")
@路由.get("/closure/reopen-tickets")
def 列闭包重开票():
    return 处置.列重开票()


@路由.get("/闭包重开审计")
@路由.get("/重开审计")
@路由.get("/reopen-audits")
@路由.get("/closure/reopen-audits")
def 列闭包重开审计():
    return 处置.列重开审计()


def _从重开请求取数据(体: Any) -> dict[str, Any]:
    if 体 is None:
        return {}
    if isinstance(体, dict):
        d = dict(体)
    else:
        d = getattr(体, "model_dump", lambda: vars(体))()
    # 兼容英文/脚手架字段。
    映射 = {
        "account_key": "账户键",
        "biz_day": "营业日",
        "generation": "世代号",
        "batch_id": "批号",
        "reason": "原因",
        "keys": "账户键集合",
    }
    for en, zh in 映射.items():
        if en in d and zh not in d:
            d[zh] = d[en]
    return d


@路由.post("/闭包重开票")
@路由.post("/重开票")
@路由.post("/闭包重开")
@路由.post("/reopen-ticket")
@路由.post("/closure/reopen-ticket")
@路由.post("/核证/差额闭包/重开票")
@路由.post("/核证/差额闭包/重开")
@路由.post("/闭包世代/重开票")
@路由.post("/闭包世代/重开")
def 开闭包重开票接口(体: dict | None = None):
    数据 = _从重开请求取数据(体)
    try:
        return 处置.开重开票请求(数据)
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.get("/夹具")
def 列夹具():
    if not 夹具目录().exists():
        return []
    return sorted(p.name for p in 夹具目录().glob("*.json"))


@路由.post("/装载")
def 执行装载(体: 装载请求):
    try:
        return 装载.装载流水(体.流水, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    名: str


@路由.post("/装载夹具/{名}")
def 装载夹具路径(名: str):
    return _装载夹具文件(名)


@路由.post("/装载夹具")
def 装载夹具体(体: 夹具请求):
    return _装载夹具文件(体.名)


def _装载夹具文件(名: str):
    path = 夹具目录() / 名
    if not path.exists():
        raise HTTPException(404, f"夹具不存在: {名}")
    data = json.loads(path.read_text(encoding="utf-8"))
    流水 = data.get("流水") or data.get("事件") or []
    return 装载.装载流水(流水, str(data.get("批次编号") or ""))


@路由.post("/抽取")
@路由.post("/extract")
def 执行抽取(体: dict | None = None):
    d = dict(体 or {})
    日 = str(d.get("营业日") or d.get("biz_day") or d.get("day") or "")
    批 = str(d.get("批次编号") or d.get("batch_id") or d.get("batch") or "")
    if not 日:
        raise HTTPException(422, "营业日不能为空")
    try:
        return 抽取.日终抽取(日, 批)
    except 作业冲突 as e:
        raise HTTPException(409, str(e)) from e
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _回放请求数据(体: dict | None) -> tuple[str, str, list[str]]:
    d = dict(体 or {})
    日 = str(d.get("营业日") or d.get("biz_day") or d.get("day") or "").strip()
    批 = str(d.get("批次编号") or d.get("batch_id") or d.get("batch") or "").strip()
    票: list[str] = []
    for k in (
        "重开票",
        "重开票号",
        "重开票集合",
        "重开票号列表",
        "ticket",
        "tickets",
        "reopen_ticket",
        "reopen_tickets",
    ):
        v = d.get(k)
        if not v:
            continue
        if isinstance(v, str):
            票.append(v)
        elif isinstance(v, list | tuple | set):
            票.extend(str(x) for x in v)
    return 日, 批, sorted(set(x.strip() for x in 票 if x and x.strip()))


@路由.post("/回放")
def 执行回放(体: dict | None = None):
    日, 批, 票 = _回放请求数据(体)
    if not 日:
        raise HTTPException(422, "营业日不能为空")
    try:
        return 回放.回放核对(日, 批, 重开票=票)
    except 作业冲突 as e:
        raise HTTPException(409, str(e)) from e
    except 闭包模块.闭包错误 as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/重置业务库")
def 重置():
    """开发冒烟用：清业务表并保留账户与台参。"""
    conn = 连接()
    try:
        for t in (
            "闭包消化记录",
            "闭包重开审计",
            "闭包重开票",
            "闭包闭合键",
            "差额闭包世代表",
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "跳变记录",
            "旁置簿",
            "装载批次",
            "纠偏工单",
            "盖写审计",
            "作业锁",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()

from app.modules import 差额闭包世代 as _bf_mod_22


def _核证请求(request: Request, 键: str | None = None, gen: str | None = None) -> dict:
    q = request.query_params
    return {
        "键": 键 or q.get("键") or q.get("key") or q.get("account_key"),
        "世代号": gen or q.get("世代号") or q.get("generation") or q.get("gen") or q.get("generation_no"),
        "批号": q.get("批号") or q.get("batch_id") or q.get("batch"),
        "营业日": q.get("营业日") or q.get("biz_day") or q.get("day"),
        "回放批次编号": q.get("回放批次编号") or q.get("replay_batch_id") or q.get("replay_batch"),
    }


@路由.get("/核证/差额闭包")
def 取核证_22(request: Request):
    f = _核证请求(request)
    conn = 连接()
    try:
        return _bf_mod_22.查现场(
            conn,
            f["键"],
            世代号=f["世代号"],
            批号=f["批号"],
            营业日=f["营业日"],
            回放批次编号=f["回放批次编号"],
        )
    finally:
        conn.close()


@路由.get("/核证/差额闭包/{gen}")
@路由.get("/核证/差额闭包世代/{gen}")
@路由.get("/closure/generations/{gen}")
@路由.get("/closure/proof/{gen}")
def 取核证世代_22(gen: str, request: Request):
    f = _核证请求(request, gen=gen)
    conn = 连接()
    try:
        return _bf_mod_22.查现场(
            conn,
            f["键"],
            世代号=f["世代号"],
            批号=f["批号"],
            营业日=f["营业日"],
            回放批次编号=f["回放批次编号"],
        )
    finally:
        conn.close()


@路由.get("/闭包世代")
@路由.get("/差额闭包世代")
@路由.get("/核证/差额闭包世代")
@路由.get("/闭包核证")
@路由.get("/闭包世代核证")
@路由.get("/差额闭包核证")
@路由.get("/核证/闭包世代")
@路由.get("/closure/generations")
@路由.get("/closure/generation")
@路由.get("/closure/proof")
@路由.get("/diff-closure/proof")
def 列闭包世代(request: Request):
    q = request.query_params
    conn = 连接()
    try:
        return _bf_mod_22.查现场(
            conn,
            q.get("键") or q.get("account_key"),
            世代号=q.get("世代号") or q.get("generation") or q.get("generation_no"),
            批号=q.get("批号") or q.get("batch_id"),
            营业日=q.get("营业日") or q.get("biz_day"),
            回放批次编号=q.get("回放批次编号") or q.get("replay_batch_id"),
        )
    finally:
        conn.close()


@路由.post("/核证/差额闭包/巡检")
def 巡检核证_22(payload: dict | None = None):
    return _bf_mod_22.巡检包(payload or {})

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

