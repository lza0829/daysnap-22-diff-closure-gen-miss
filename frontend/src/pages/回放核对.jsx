import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function 回放核对() {
  const [日, set日] = useState("2026-11-22");
  const [批, set批] = useState([]);
  const [讯, set讯] = useState("");
  const [处置图, set处置图] = useState({});
  const [忙, set忙] = useState("");

  const 刷新 = async () => {
    const 列表 = await api.回放批次();
    set批(列表);
    const 视图 = {};
    await Promise.all(
      列表.slice(0, 10).map(async (r) => {
        视图[r.编号] = await api.回放处置(r.编号);
      })
    );
    set处置图(视图);
  };

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 跑 = () =>
    api
      .回放(日)
      .then((r) =>
        set讯(
          `批次 ${r.批次编号} 通过=${r.通过} 相符=${r.相符} 差额=${r.差额} 工单=${r.工单数} ` +
            `改镜=${r.是否改镜}（口径：${r.处置口径}——回放不改镜，差额落纠偏工单）`
        )
      )
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));

  const 看处置 = async (编号) => {
    set忙(编号);
    try {
      const v = await api.回放处置(编号);
      set处置图((m) => ({ ...m, [编号]: v }));
    } catch (e) {
      set讯(String(e.message || e));
    } finally {
      set忙("");
    }
  };

  return (
    <div>
      <h1 className="题">回放核对</h1>
      <p className="述">
        前日快照加当日流水重算对照当日镜。检出差额即与差额行同票落
        <b>纠偏工单</b>，回放事务<b>不改镜</b>（是否改镜恒为否）；与同日抽取盖写互斥，交错即失败闭。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} />
        <button className="水" onClick={跑}>
          执行回放
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>刷新批次</button>
        <Link className="次" to="/纠偏工单" style={{ marginLeft: 8 }}>
          去纠偏工单页 →
        </Link>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}
      <div className="段">
        <h2 className="段题">回放批次与处置</h2>
        {!批.length ? (
          <div className="空态">尚无回放</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>报告</th>
                <th>是否改镜</th>
                <th>工单</th>
                <th>处置</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => {
                const v = 处置图[r.编号];
                let 报告 = r.报告;
                if (typeof 报告 === "string") {
                  try { 报告 = JSON.parse(报告); } catch {  }
                }
                return (
                  <tr key={r.编号}>
                    <td>{r.编号}</td>
                    <td>{r.营业日}</td>
                    <td>{typeof 报告 === "object" ? JSON.stringify(报告) : 报告}</td>
                    <td>{v ? (v.是否改镜 ? "是" : "否（不改镜）") : "—"}</td>
                    <td>{v ? `${v.待处理工单数}/${v.工单数} 待处理` : "—"}</td>
                    <td>
                      {v ? (
                        v.工单数 ? (
                          <Link to="/纠偏工单">查工单</Link>
                        ) : (
                          "无差额无工单"
                        )
                      ) : (
                        <button className="次" disabled={忙 === r.编号} onClick={() => 看处置(r.编号)}>
                          读处置
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
