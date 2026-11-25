"""差额闭包世代写闸测试（选定架构：乙·闭包可重开票）。"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import 连接
from app.modules import 差额闭包世代 as 闭包
from app.services import 处置, 抽取, 装载, 回放
from app.services.作业闸 import 作业冲突

夹具根 = ROOT.parent / "fixtures"
日 = "2026-11-22"
前日 = "2026-11-21"


@pytest.fixture()
def 临时库(tmp_path, monkeypatch):
    monkeypatch.setenv("数据目录", str(tmp_path / "data"))
    monkeypatch.setenv("夹具目录", str(夹具根))
    from app import seed

    seed.初始化库()
    yield tmp_path


@pytest.fixture()
def 客户端(tmp_path, monkeypatch):
    monkeypatch.setenv("数据目录", str(tmp_path / "data"))
    monkeypatch.setenv("夹具目录", str(夹具根))
    from app import seed
    from app.main import app
    from app.services import 作业闸

    seed.初始化库()
    with TestClient(app) as c:
        yield c, 作业闸


def _读夹具(名: str) -> dict:
    return json.loads((夹具根 / 名).read_text(encoding="utf-8"))


def _造差(名: str) -> dict:
    数据 = _读夹具(名)
    装载.装载流水(数据["流水"], 数据["批次编号"])
    抽取.日终抽取(前日)
    抽取.日终抽取(日)
    conn = 连接()
    try:
        for t in 数据["人为篡改"]:
            conn.execute(
                "UPDATE 周期快照 SET 余额=余额+? WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (t["加成"], t["账户键"], t["营业日"]),
            )
        conn.commit()
    finally:
        conn.close()
    return 数据


def _表数(表: str, where: str = "") -> int:
    conn = 连接()
    try:
        sql = f"SELECT COUNT(*) AS c FROM {表}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()["c"])
    finally:
        conn.close()


def _快照余额(键: str) -> int:
    conn = 连接()
    try:
        return int(
            conn.execute(
                "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (键, 日),
            ).fetchone()["余额"]
        )
    finally:
        conn.close()


def test_处置同票写世代_二次回放消化_开放清单无原键(临时库):
    _造差("闭包世代造差处置样例.json")
    r1 = 回放.回放核对(日, "闭包-首次回放")
    assert r1["差额"] == 2 and r1["工单数"] == 2

    结果 = 处置.批量核账闭环("闭包-首次回放")
    assert 结果["世代集合"] and len(结果["世代集合"]) == 1
    gen = 结果["世代集合"][0]
    assert gen["批号"] == "闭包-首次回放"
    assert set(gen["闭合键"]) == {"户甲", "户乙"}
    assert gen["键哈希"] == 闭包.键集合哈希(["户甲", "户乙"])
    assert gen["世代号"]

    assert _表数("差额闭包世代表") == 1
    assert _表数("闭包闭合键", "状态='已闭合'") == 2
    conn = 连接()
    try:
        现场 = 闭包.查现场(conn, 批号="闭包-首次回放")
        assert 现场["valid"] is True
        assert 现场["架构"] == "乙"
        g = 现场["世代集合"][0]
        assert g["世代号"] == gen["世代号"]
        assert set(g["闭合键"]) == {"户甲", "户乙"}
        assert g["重开否"] is False and g["valid"] is True
        assert {"世代号", "批号", "闭合键", "重开否", "valid"} <= set(g)
    finally:
        conn.close()

    r2 = 回放.回放核对(日, "闭包-二次回放")
    assert r2["差额"] == 0
    assert r2["工单数"] == 0
    assert r2["已闭合"] == 2
    assert set(r2["闭合键集合"]) == {"户甲", "户乙"}
    assert r2["通过"] is True

    assert 处置.列差额行(回放批次编号="闭包-二次回放", 仅开放=True) == []
    assert 处置.开放差额清单("闭包-二次回放") == []
    assert 处置.列差额行(仅开放=True) == []
    assert _表数("差额行", "回放批次编号='闭包-二次回放'") == 0
    assert _表数("闭包消化记录", "回放批次编号='闭包-二次回放' AND 结果='已闭合'") == 2


def test_无票不重开_持重开票原键重现_写重开审计(临时库):
    _造差("闭包重开票样例.json")
    r1 = 回放.回放核对(日, "重开-首次回放")
    assert r1["差额"] == 1

    视图 = 处置.取回放处置("重开-首次回放")
    wid = 视图["工单集合"][0]["工单号"]
    closed = 处置.人工核账闭环(wid)
    assert closed["闭包世代号"]

    r2 = 回放.回放核对(日, "重开-无票回放")
    assert r2["差额"] == 0 and r2["已闭合"] == 1
    assert 处置.列差额行(仅开放=True) == []

    票 = 处置.开重开票请求({"营业日": 日, "账户键": "户甲", "原因": "测试重开"})
    assert 票["票号"].startswith("重开票-") and 票["状态"] == "有效"

    r3 = 回放.回放核对(日, "重开-持票回放", 重开票=票["票号"])
    assert r3["差额"] == 1 and r3["工单数"] == 1 and r3["重开数"] == 1
    assert r3["重开键集合"] == ["户甲"]

    开放 = 处置.列差额行(仅开放=True)
    assert [r["账户键"] for r in 开放] == ["户甲"]
    assert 开放[0]["回放批次编号"] == "重开-持票回放"
    assert 开放[0]["重开票号"] == 票["票号"]
    assert _表数("闭包重开审计", f"票号='{票['票号']}' AND 账户键='户甲'") == 1

    conn = 连接()
    try:
        现场 = 闭包.查现场(conn, "户甲")
        assert 现场["valid"] is True
        g = next(x for x in 现场["世代集合"] if x["世代号"] == closed["闭包世代号"])
        assert g["重开否"] is True and "户甲" in g["重开键"]
    finally:
        conn.close()


def test_删世代再回放失败闭且无新批落库(临时库):
    _造差("闭包世代造差处置样例.json")
    回放.回放核对(日, "删世代-首次")
    处置.批量核账闭环("删世代-首次")

    conn = 连接()
    try:
        conn.execute("DELETE FROM 差额闭包世代表")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(闭包.闭包世代缺失):
        回放.回放核对(日, "删世代-二次")
    assert _表数("回放批次", "编号='删世代-二次'") == 0
    assert _表数("差额行", "回放批次编号='删世代-二次'") == 0
    assert _表数("作业锁") == 0


def test_世代写库失败则处置整票回滚(临时库):
    _造差("闭包重开票样例.json")
    回放.回放核对(日, "原子-首次")
    视图 = 处置.取回放处置("原子-首次")
    wid = 视图["工单集合"][0]["工单号"]
    原镜 = _快照余额("户甲")

    conn = 连接()
    try:
        conn.execute("DROP TABLE 差额闭包世代表")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(Exception):
        处置.人工核账闭环(wid)

    assert _快照余额("户甲") == 原镜
    assert _表数("盖写审计") == 0
    assert _表数("纠偏工单", f"工单号='{wid}' AND 状态='待处理'") == 1


def test_二次回放持锁时抽取冲撞失败闭_不无世代改闭合键(临时库, monkeypatch):
    from app.services import 作业闸

    _造差("闭包世代并发样例.json")
    回放.回放核对(日, "闭包并发-首次")
    处置.批量核账闭环("闭包并发-首次")
    闭合后余额 = _快照余额("户甲")
    assert 闭合后余额 == 7000

    进入 = threading.Event()
    放行 = threading.Event()

    def 钩子(营业日, 持锁者):
        if 持锁者 == "回放:闭包并发-二次":
            进入.set()
            放行.wait(timeout=10)

    monkeypatch.setattr(作业闸, "持锁回调", 钩子)
    错误 = []

    def 跑二次回放():
        try:
            回放.回放核对(日, "闭包并发-二次")
        except Exception as e:  # pragma: no cover - 失败路径断言用
            错误.append(e)

    t = threading.Thread(target=跑二次回放)
    t.start()
    assert 进入.wait(timeout=10)

    with pytest.raises(作业冲突):
        抽取.日终抽取(日, "闭包并发-冲撞抽取")
    assert _快照余额("户甲") == 闭合后余额
    assert _表数("抽取批次", "编号='闭包并发-冲撞抽取'") == 0

    放行.set()
    t.join(timeout=10)
    assert not 错误
    assert _表数("作业锁") == 0
    assert _表数("闭包消化记录", "回放批次编号='闭包并发-二次' AND 结果='已闭合'") == 1


def test_闭包世代核证与重开票_http_开放清单(客户端):
    c, _ = 客户端
    数据 = _读夹具("闭包重开票样例.json")
    r = c.post("/api/装载夹具", json={"名": "闭包重开票样例.json"})
    assert r.status_code == 200
    assert c.post("/api/抽取", json={"营业日": 前日}).status_code == 200
    assert c.post("/api/抽取", json={"营业日": 日}).status_code == 200

    conn = 连接()
    try:
        for t in 数据["人为篡改"]:
            conn.execute(
                "UPDATE 周期快照 SET 余额=余额+? WHERE 账户键=? AND 营业日=?",
                (t["加成"], t["账户键"], t["营业日"]),
            )
        conn.commit()
    finally:
        conn.close()

    assert c.post("/api/回放", json={"营业日": 日, "批次编号": "HTTP-首次"}).status_code == 200
    closed = c.post("/api/回放处置/HTTP-首次/闭环", json={})
    assert closed.status_code == 200, closed.text
    gen = closed.json()["世代集合"][0]["世代号"]

    proof = c.get(f"/api/核证/差额闭包?世代号={gen}")
    assert proof.status_code == 200
    p = proof.json()
    assert p["valid"] is True and p["世代号"] == gen
    assert p["闭合键"] == ["户甲"] and p["重开否"] is False

    again = c.post("/api/回放", json={"营业日": 日, "批次编号": "HTTP-无票"})
    assert again.status_code == 200 and again.json()["已闭合"] == 1
    assert c.get("/api/开放差额").json() == []
    assert c.get("/api/差额").json() == []
    assert len(c.get("/api/差额?全部=true").json()) >= 1

    ticket = c.post(
        "/api/闭包重开票",
        json={"营业日": 日, "账户键": "户甲", "原因": "HTTP 重开"},
    )
    assert ticket.status_code == 200, ticket.text
    tid = ticket.json()["票号"]

    reopened = c.post(
        "/api/回放",
        json={"营业日": 日, "批次编号": "HTTP-持票", "重开票": tid},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["重开数"] == 1
    open_rows = c.get("/api/开放差额").json()
    assert [r["账户键"] for r in open_rows] == ["户甲"]

    proof2 = c.get(f"/api/核证/差额闭包/{gen}").json()
    assert proof2["重开否"] is True and proof2["valid"] is True
    assert any(a["票号"] == tid for a in c.get("/api/闭包重开审计").json())

    conn = 连接()
    try:
        conn.execute("DELETE FROM 差额闭包世代表")
        conn.commit()
    finally:
        conn.close()
    bad = c.post("/api/回放", json={"营业日": 日, "批次编号": "HTTP-删世代"})
    assert bad.status_code == 409
    assert "闭包世代" in bad.json()["detail"]
