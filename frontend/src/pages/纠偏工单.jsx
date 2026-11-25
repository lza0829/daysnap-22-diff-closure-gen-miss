import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function 纠偏工单() {
  const [行, set行] = useState([]);
  const [审计, set审计] = useState([]);
  const [仅待处理, set仅待处理] = useState(false);
  const [讯, set讯] = useState("");

  const 刷新 = () =>
    Promise.all([api.工单(仅待处理), api.盖写审计()]).then(([t, a]) => {
      set行(t);
      set审计(a);
    });

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, [仅待处理]);

  const 闭环 = (工单号) => {
    if (!window.confirm(`按工单回放余额人工核账闭环 ${工单号}？镜将被修正并落盖写审计，同时同票写闭包世代。`)) return;
    api
      .工单闭环(工单号)
      .then((r) =>
        set讯(
          `已闭环：${r.账户键} ${r.营业日} 镜 ${r.旧余额} → ${r.新余额}；闭包世代 ${r.闭包世代号 || r.世代号}`
        )
      )
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));
  };

  const 全部闭环 = () => {
    const ids = 行.filter((t) => t.状态 === "待处理").map((t) => t.工单号);
    if (!ids.length) return;
    if (!window.confirm(`将同票闭环当前 ${ids.length} 张待处理工单，并按批次写闭包世代，继续？`)) return;
    api
      .批量闭环({ 工单号集合: ids })
      .then((r) => {
        const gens = (r.世代集合 || []).map((g) => g.世代号).join("、");
        set讯(`已批量闭环 ${r.工单数} 张工单；闭包世代：${gens}`);
      })
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));
  };

  return (
    <div>
      <h1 className="题">纠偏工单</h1>
      <p className="述">
        回放检出差额的处置事实，与差额行/回放批同票落库、不可变（检出快照余额、回放余额、差额冻结）。
        回放本身<b>禁止改镜</b>；闭环两条路——人工核账（修正镜+盖写审计）或同日抽取盖写自动闭环。
      </p>
      <div className="行">
        <label>
          <input type="checkbox" checked={仅待处理} onChange={(e) => set仅待处理(e.target.checked)} />{" "}
          仅看待处理
        </label>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>刷新</button>
        <button className="水" onClick={全部闭环}>同票闭环当前待处理</button>
        <Link className="次" to="/差额闭包">闭包核证</Link>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}
      {!行.length ? (
        <div className="空态">暂无纠偏工单（回放无差额即不落单）</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>工单号</th>
              <th>回放批次</th>
              <th>账户键</th>
              <th>营业日</th>
              <th>检出快照余额</th>
              <th>回放余额</th>
              <th>差额</th>
              <th>状态</th>
              <th>闭包世代</th>
              <th>处理方式</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {行.map((t) => (
              <tr key={t.工单号}>
                <td>{t.工单号}</td>
                <td>{t.回放批次编号}</td>
                <td>{t.账户键}</td>
                <td>{t.营业日}</td>
                <td>{t.检出快照余额 ?? "—（缺镜）"}</td>
                <td>{t.回放余额 ?? "—"}</td>
                <td>{t.差额 ?? "—"}</td>
                <td>{t.状态}</td>
                <td>{t.闭包世代号 || t.最新闭包世代号 || "—"}</td>
                <td>{t.处理方式 || "—"}</td>
                <td>
                  {t.状态 === "待处理" ? (
                    <button className="水" onClick={() => 闭环(t.工单号)}>
                      人工核账闭环
                    </button>
                  ) : (
                    "已闭环"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="段">
        <h2 className="段题">盖写审计（镜的每次改动都可追溯，回放不在此列）</h2>
        {!审计.length ? (
          <div className="空态">暂无盖写</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>账户键</th>
                <th>营业日</th>
                <th>旧余额</th>
                <th>新余额</th>
                <th>来源</th>
                <th>批次编号</th>
                <th>时刻</th>
              </tr>
            </thead>
            <tbody>
              {审计.map((a) => (
                <tr key={a.编号}>
                  <td>{a.账户键}</td>
                  <td>{a.营业日}</td>
                  <td>{a.旧余额 ?? "—"}</td>
                  <td>{a.新余额}</td>
                  <td>{a.来源}</td>
                  <td>{a.批次编号}</td>
                  <td>{a.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
