"""回放处置测试 —— 全局口径：快照权威工单（回放不改镜）。

覆盖：
1. 人为改快照制造差额再回放：甲人工核账闭环（镜改回、二次回放通过）；
   乙不处理（镜不变、工单可查、二次回放仍差额）；
2. 回放处置只读接口：批号、是否改镜=false、工单集合、valid；
3. 回放与同日抽取并发交错：持锁期间对侧 409 失败闭，同票回滚无半截改镜；
4. 抽取盖写自动闭环工单；同票事务原子性。
"""

import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import 连接  
from app.modules import 回放权威择定  
from app.services import 回放, 抽取, 处置, 装载, 作业闸  
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


def _读夹具(名: str) -> dict:
    return json.loads((夹具根 / 名).read_text(encoding="utf-8"))


def _装流水(数据: dict) -> None:
    装载.装载流水(数据["流水"], 数据["批次编号"])


def _篡改快照(数据: dict) -> None:
    """模拟人为直接改有效快照余额（绕过抽取），制造回放差额。"""
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


def _快照余额(账户键: str, 营业日: str) -> int:
    conn = 连接()
    try:
        r = conn.execute(
            "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (账户键, 营业日),
        ).fetchone()
        return int(r["余额"])
    finally:
        conn.close()


def _表数(表: str, where: str = "") -> int:
    conn = 连接()
    try:
        sql = f"SELECT COUNT(*) AS c FROM {表}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()["c"])
    finally:
        conn.close()




def test_造差额回放_甲闭环乙挂起(临时库):
    assert 回放权威择定.启用 is True
    assert 回放权威择定.权威口径 == "快照权威工单"
    assert 回放权威择定.是否改镜 is False

    数据 = _读夹具("回放差额样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    抽取.日终抽取(日)
    
    assert _快照余额("户甲", 日) == 13000
    assert _快照余额("户乙", 日) == 7000

    _篡改快照(数据)
    甲被改 = _快照余额("户甲", 日)
    乙被改 = _快照余额("户乙", 日)
    assert 甲被改 == 13000 + 9999
    assert 乙被改 == 7000 + 7777

    结果 = 回放.回放核对(日, "回放批-造差-1")
    assert 结果["通过"] is False
    assert 结果["差额"] == 2
    assert 结果["工单数"] == 2
    assert 结果["是否改镜"] is False

    
    assert _快照余额("户甲", 日) == 甲被改
    assert _快照余额("户乙", 日) == 乙被改
    assert _表数("盖写审计") == 0

    
    处置视图 = 处置.取回放处置("回放批-造差-1")
    assert 处置视图["valid"] is True
    assert 处置视图["是否改镜"] is False
    assert 处置视图["处置口径"] == "快照权威工单"
    assert len(处置视图["工单集合"]) == 2
    assert 处置视图["待处理工单数"] == 2
    工单by户 = {t["账户键"]: t for t in 处置视图["工单集合"]}
    assert 工单by户["户甲"]["检出快照余额"] == 甲被改
    assert 工单by户["户甲"]["回放余额"] == 13000
    assert 工单by户["户甲"]["差额"] == 9999
    assert 工单by户["户乙"]["状态"] == "待处理"
    
    assert 处置.取回放处置("不存在的批")["valid"] is False

    
    甲工单 = 工单by户["户甲"]["工单号"]
    闭环 = 处置.人工核账闭环(甲工单)
    assert 闭环["新余额"] == 13000
    assert 闭环["盖写审计"] is True
    assert _快照余额("户甲", 日) == 13000  
    assert _快照余额("户乙", 日) == 乙被改  
    assert _表数("盖写审计", "来源='人工核账工单'") == 1
    assert _表数("纠偏工单", "状态='已闭环' AND 账户键='户甲'") == 1
    
    conn = 连接()
    try:
        t = conn.execute("SELECT * FROM 纠偏工单 WHERE 工单号=?", (甲工单,)).fetchone()
        assert t["检出快照余额"] == 甲被改
        assert t["回放余额"] == 13000
        assert t["处理方式"] == "人工核账"
    finally:
        conn.close()
    
    with pytest.raises(ValueError):
        处置.人工核账闭环(甲工单)

    
    二次 = 回放.回放核对(日, "回放批-造差-2")
    assert 二次["通过"] is False
    assert 二次["差额"] == 1
    assert 二次["工单数"] == 1
    assert _快照余额("户乙", 日) == 乙被改
    assert _表数("纠偏工单", "状态='待处理' AND 账户键='户乙'") >= 1
    乙视图 = 处置.取回放处置("回放批-造差-2")
    assert 乙视图["工单集合"][0]["账户键"] == "户乙"

    
    抽 = 抽取.日终抽取(日, "抽取批-重抽-1")
    assert 抽["盖写"] >= 1
    assert 抽["工单闭环"] >= 1
    assert _快照余额("户乙", 日) == 7000
    assert _表数("纠偏工单", "状态='待处理'") == 0
    assert _表数("盖写审计", "来源='抽取盖写闭环工单'") >= 1
    三次 = 回放.回放核对(日, "回放批-造差-3")
    assert 三次["通过"] is True
    assert 三次["差额"] == 0
    assert 三次["工单数"] == 0
    
    assert _表数("作业锁") == 0




def test_回放持锁时抽取失败闭(临时库, monkeypatch):
    数据 = _读夹具("回放抽取并发样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    抽取.日终抽取(日)
    _篡改快照(数据)
    原镜 = _快照余额("户甲", 日)

    放行 = threading.Event()
    进入 = threading.Event()

    def 钩子(营业日, 持锁者):
        if 持锁者.startswith("回放:"):
            进入.set()
            放行.wait(timeout=10)

    monkeypatch.setattr(作业闸, "持锁回调", 钩子)
    回放错误 = []

    def 跑回放():
        try:
            回放.回放核对(日, "回放批-并发-1")
        except Exception as e:  
            回放错误.append(e)

    t = threading.Thread(target=跑回放)
    t.start()
    assert 进入.wait(timeout=10), "回放未在持锁态阻塞"

    
    with pytest.raises(作业冲突):
        抽取.日终抽取(日, "抽取批-并发-撞")
    assert _快照余额("户甲", 日) == 原镜          
    assert _表数("盖写审计") == 0                  
    assert _表数("抽取批次", "编号='抽取批-并发-撞'") == 0

    放行.set()
    t.join(timeout=10)
    assert not 回放错误
    assert _表数("差额行", "回放批次编号='回放批-并发-1'") >= 1
    assert _表数("纠偏工单", "回放批次编号='回放批-并发-1'") >= 1
    assert _表数("回放批次", "编号='回放批-并发-1'") == 1
    assert _快照余额("户甲", 日) == 原镜           
    assert _表数("作业锁") == 0


def test_抽取持锁时回放失败闭_同票全滚(临时库, monkeypatch):
    数据 = _读夹具("回放抽取并发样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    抽取.日终抽取(日)
    _篡改快照(数据)
    原镜 = _快照余额("户甲", 日)

    放行 = threading.Event()
    进入 = threading.Event()

    def 钩子(营业日, 持锁者):
        if 持锁者.startswith("抽取:"):
            进入.set()
            放行.wait(timeout=10)

    monkeypatch.setattr(作业闸, "持锁回调", 钩子)
    抽取错误 = []

    def 跑抽取():
        try:
            抽取.日终抽取(日, "抽取批-并发-持")
        except Exception as e:  
            抽取错误.append(e)

    t = threading.Thread(target=跑抽取)
    t.start()
    assert 进入.wait(timeout=10)

    
    with pytest.raises(作业冲突):
        回放.回放核对(日, "回放批-并发-撞")
    assert _表数("差额行", "回放批次编号='回放批-并发-撞'") == 0
    assert _表数("纠偏工单", "回放批次编号='回放批-并发-撞'") == 0
    assert _表数("回放批次", "编号='回放批-并发-撞'") == 0
    assert _快照余额("户甲", 日) == 原镜

    放行.set()
    t.join(timeout=10)
    assert not 抽取错误
    assert _表数("作业锁") == 0

    
    _篡改快照(数据)
    r = 回放.回放核对(日, "回放批-并发-补")
    assert r["通过"] is False and r["工单数"] >= 1
    assert _表数("作业锁") == 0




def test_处置与差额行同票(临时库):
    数据 = _读夹具("回放差额样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    抽取.日终抽取(日)
    _篡改快照(数据)

    
    conn = 连接()
    try:
        conn.execute("DROP TABLE 纠偏工单")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(Exception):  
        回放.回放核对(日, "回放批-同票-失败")
    assert _表数("差额行", "回放批次编号='回放批-同票-失败'") == 0
    assert _表数("回放批次", "编号='回放批-同票-失败'") == 0
    assert _表数("作业锁") == 0


def test_无差额回放不落工单(临时库):
    数据 = _读夹具("回放差额样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    抽取.日终抽取(日)  
    r = 回放.回放核对(日, "回放批-干净")
    assert r["通过"] is True
    assert r["工单数"] == 0
    视图 = 处置.取回放处置("回放批-干净")
    assert 视图["valid"] is True
    assert 视图["工单集合"] == []
    assert 视图["是否改镜"] is False
    assert _表数("作业锁") == 0


def test_缺当日快照同样落工单_抽取闭环(临时库):
    """缺镜差额不得只回数字：落缺镜工单（快照余额/差额为空），抽取补镜后闭环。"""
    数据 = _读夹具("回放差额样例.json")
    _装流水(数据)
    抽取.日终抽取(前日)
    
    r = 回放.回放核对(日, "回放批-缺镜")
    assert r["通过"] is False
    assert r["无快照"] == 3
    assert r["工单数"] == 3
    视图 = 处置.取回放处置("回放批-缺镜")
    assert all(t["检出快照余额"] is None and t["差额"] is None for t in 视图["工单集合"])
    assert all(t["回放余额"] is not None for t in 视图["工单集合"])
    
    工单号 = 视图["工单集合"][0]["工单号"]
    with pytest.raises(ValueError):
        处置.人工核账闭环(工单号)
    
    抽 = 抽取.日终抽取(日, "抽取批-补镜")
    assert 抽["工单闭环"] == 3
    assert _表数("纠偏工单", "状态='已闭环'") == 3
    conn = 连接()
    try:
        审计 = conn.execute(
            "SELECT 旧余额, 新余额 FROM 盖写审计 WHERE 来源='抽取盖写闭环工单'"
        ).fetchall()
        assert len(审计) == 3 and all(a["旧余额"] is None for a in 审计)
    finally:
        conn.close()
    再回放 = 回放.回放核对(日, "回放批-缺镜补齐")
    assert 再回放["通过"] is True and 再回放["工单数"] == 0
    assert _表数("作业锁") == 0
