"""HTTP 接口层：回放处置只读契约、作业冲突 409、工单闭环。"""

import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

夹具根 = ROOT.parent / "fixtures"
日 = "2026-11-22"
前日 = "2026-11-21"


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


def _造差额库(客户端):
    c, _ = 客户端
    数据 = json.loads((夹具根 / "回放差额样例.json").read_text(encoding="utf-8"))
    r = c.post("/api/装载夹具", json={"名": "回放差额样例.json"})
    assert r.status_code == 200
    c.post("/api/抽取", json={"营业日": 前日})
    c.post("/api/抽取", json={"营业日": 日})
    
    from app.db import 连接

    conn = 连接()
    try:
        for t in 数据["人为篡改"]:
            conn.execute(
                "UPDATE 周期快照 SET 余额 = 余额 + ? WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (t["加成"], t["账户键"], t["营业日"]),
            )
        conn.commit()
    finally:
        conn.close()
    return 数据


def test_回放处置只读接口契约(客户端):
    c, _ = 客户端
    _造差额库(客户端)
    r = c.post("/api/回放", json={"营业日": 日, "批次编号": "接口批-1"})
    assert r.status_code == 200
    体 = r.json()
    assert 体["通过"] is False and 体["工单数"] == 2
    assert 体["是否改镜"] is False

    视图 = c.get("/api/回放处置/接口批-1").json()
    assert 视图["valid"] is True
    assert 视图["是否改镜"] is False
    assert 视图["处置口径"] == "快照权威工单"
    assert 视图["工单数"] == 2
    assert { "账户键", "营业日", "检出快照余额", "回放余额", "差额", "状态" } <= set(
        视图["工单集合"][0]
    )
    assert c.get("/api/回放处置/幽灵批").json()["valid"] is False
    assert c.get("/api/工单?仅待处理=true").json().__len__() == 2

    
    快照 = {r["账户键"]: r["余额"] for r in c.get(f"/api/快照?营业日={日}").json()}
    工单by户 = {t["账户键"]: t for t in 视图["工单集合"]}
    assert 快照["户甲"] == 工单by户["户甲"]["检出快照余额"]

    
    甲工单 = 工单by户["户甲"]["工单号"]
    闭环 = c.post("/api/工单闭环", json={"工单号": 甲工单})
    assert 闭环.status_code == 200
    assert 闭环.json()["状态"] == "已闭环"
    审计 = c.get("/api/盖写审计").json()
    assert any(a["来源"] == "人工核账工单" for a in 审计)
    
    assert c.post("/api/工单闭环", json={"工单号": 甲工单}).status_code == 409
    
    assert c.post("/api/工单闭环", json={"工单号": "幽灵工单"}).status_code == 404


def test_回放抽取并发_接口409失败闭(客户端):
    c, 作业闸 = 客户端
    _造差额库(客户端)
    放行 = threading.Event()
    进入 = threading.Event()

    def 钩子(营业日, 持锁者):
        if 持锁者.startswith("回放:"):
            进入.set()
            放行.wait(timeout=10)

    作业闸.持锁回调 = 钩子
    错 = []

    def 跑回放():
        try:
            c.post("/api/回放", json={"营业日": 日, "批次编号": "接口并发-回放"})
        except Exception as e:  
            错.append(e)

    t = threading.Thread(target=跑回放)
    t.start()
    assert 进入.wait(timeout=10)
    撞 = c.post("/api/抽取", json={"营业日": 日, "批次编号": "接口并发-抽取"})
    assert 撞.status_code == 409
    放行.set()
    t.join(timeout=10)
    assert not 错
    作业闸.持锁回调 = None
    
    assert c.get("/api/回放处置/接口并发-回放").json()["valid"] is True
    from app.db import 连接

    conn = 连接()
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM 作业锁").fetchone()["c"] == 0
    finally:
        conn.close()
