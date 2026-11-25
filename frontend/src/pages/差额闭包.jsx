import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function 核证差额闭包() {
  const [现场, set现场] = useState(null);
  const [键, set键] = useState("");
  const [营业日, set营业日] = useState("2026-11-22");
  const [账户键, set账户键] = useState("户甲");
  const [原因, set原因] = useState("台面签发重开票");
  const [err, setErr] = useState("");
  const [讯, set讯] = useState("");
  const [忙, set忙] = useState(false);

  const reload = (k = 键) => {
    setErr("");
    return api
      .闭包核证(k ? { 键: k } : {})
      .then(set现场)
      .catch((e) => setErr(String(e.message || e)));
  };

  useEffect(() => {
    reload("").catch(() => {});
  }, []);

  const 开票 = async () => {
    set忙(true);
    set讯("");
    try {
      const t = await api.开重开票({ 营业日, 账户键, 原因 });
      set讯(`已签发 ${t.票号}；到回放页持票回放，或在回放请求中传入该票号。`);
      await reload(账户键);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      set忙(false);
    }
  };

  const gens = 现场?.世代集合 || 现场?.闭包世代 || [];

  return (
    <div>
      <h1 className="题">差额闭包核证</h1>
      <p className="述">
        当前选定架构：<b>乙 · 闭包可重开票</b>。处置成功与闭包世代同票落库；二次回放默认消化世代、
        不把已闭合键放进开放差额清单，持有效重开票才允许原键重现并写重开审计。
      </p>

      <div className="行">
        <input
          value={键}
          onChange={(e) => set键(e.target.value)}
          placeholder="按账户键/批号/世代号过滤，可空"
        />
        <button className="次" onClick={() => reload().catch((e) => setErr(String(e.message || e)))}>
          刷新核证
        </button>
        <Link className="次" to="/回放核对">去二次回放 →</Link>
        <Link className="次" to="/差额清单">开放差额页 →</Link>
      </div>

      <div className="段 卡片">
        <h2 className="段题">签发重开票</h2>
        <div className="行">
          <input value={营业日} onChange={(e) => set营业日(e.target.value)} placeholder="营业日" />
          <input value={账户键} onChange={(e) => set账户键(e.target.value)} placeholder="账户键" />
          <input value={原因} onChange={(e) => set原因(e.target.value)} placeholder="原因" />
          <button className="水" disabled={忙} onClick={开票}>签发并重载核证</button>
        </div>
        {讯 ? <p className="讯">{讯}</p> : null}
        {err ? <pre className="码">{err}</pre> : null}
      </div>

      <div className="段">
        <h2 className="段题">世代（{gens.length}）</h2>
        {!gens.length ? (
          <div className="空态">尚无闭包世代；先在纠偏工单页处置成功一次。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>世代号</th>
                <th>源批号</th>
                <th>营业日</th>
                <th>闭合键</th>
                <th>哈希</th>
                <th>重开否</th>
                <th>valid</th>
              </tr>
            </thead>
            <tbody>
              {gens.map((g) => (
                <tr key={g.世代号}>
                  <td>{g.世代号}</td>
                  <td>{g.批号}</td>
                  <td>{g.营业日 || "—"}</td>
                  <td>{(g.闭合键 || []).join("、")}</td>
                  <td title={g.键哈希}>{String(g.键哈希 || "").slice(0, 12)}…</td>
                  <td>{g.重开否 ? `是（${(g.重开键 || []).join("、") || "见审计"}）` : "否"}</td>
                  <td>{g.valid ? "true" : "false"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="段">
        <h2 className="段题">只读现场 JSON</h2>
        <pre className="码">{现场 ? JSON.stringify(现场, null, 2) : "（空）"}</pre>
      </div>
    </div>
  );
}
