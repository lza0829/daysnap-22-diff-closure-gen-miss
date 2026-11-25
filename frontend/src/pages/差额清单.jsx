import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function 差额清单() {
  const [行, set行] = useState([]);
  const [仅开放, set仅开放] = useState(true);
  const [讯, set讯] = useState("");
  const [忙, set忙] = useState("");

  const 刷新 = (open = 仅开放) => {
    const q = open ? "?开放=true" : "?全部=true";
    return api.差额(q).then(set行);
  };

  useEffect(() => {
    刷新(仅开放).catch((e) => set讯(String(e.message || e)));
  }, [仅开放]);

  const 开重开票 = async (r) => {
    set忙(r.编号);
    set讯("");
    try {
      const t = await api.开重开票({
        营业日: r.营业日,
        账户键: r.账户键,
        世代号: r.闭包世代号 || r.最新闭包世代号 || undefined,
        原因: `由差额清单第 ${r.编号} 行申请重开`,
      });
      set讯(`已签发重开票 ${t.票号}；请到回放页用该票号执行二次回放。`);
    } catch (e) {
      set讯(String(e.message || e));
    } finally {
      set忙("");
    }
  };

  return (
    <div>
      <h1 className="题">差额清单</h1>
      <p className="述">
        默认展示<b>开放差额</b>：对应工单仍待处理。首次处置成功后同票写闭包世代；无重开票的二次回放
        会消化世代，原闭合键不再进入本清单。历史差额行仍保留，可取消“仅开放”查看。
      </p>
      <div className="行">
        <label>
          <input
            type="checkbox"
            checked={仅开放}
            onChange={(e) => set仅开放(e.target.checked)}
          />{" "}
          仅开放清单
        </label>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新
        </button>
        <Link className="次" to="/纠偏工单">去处置工单 →</Link>
        <Link className="次" to="/差额闭包">闭包核证 →</Link>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}
      {!行.length ? (
        <div className="空态">{仅开放 ? "开放差额清单为空：已闭合键由闭包世代接管" : "暂无差额行"}</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>回放批次</th>
              <th>账户键</th>
              <th>营业日</th>
              <th>快照余额</th>
              <th>回放余额</th>
              <th>差额</th>
              <th>开放</th>
              <th>闭包世代</th>
              <th>原因</th>
              <th>重开</th>
            </tr>
          </thead>
          <tbody>
            {行.map((r) => (
              <tr key={r.编号}>
                <td>{r.回放批次编号}</td>
                <td>{r.账户键}</td>
                <td>{r.营业日}</td>
                <td>{r.快照余额 ?? "—"}</td>
                <td>{r.回放余额 ?? "—"}</td>
                <td>{r.差额 ?? "—"}</td>
                <td>{r.开放 === false ? "已闭合/历史" : "开放"}</td>
                <td>{r.闭包世代号 || r.最新闭包世代号 || "—"}</td>
                <td>{r.原因}</td>
                <td>
                  <button
                    className="次"
                    disabled={忙 === r.编号}
                    onClick={() => 开重开票(r)}
                  >
                    开重开票
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
