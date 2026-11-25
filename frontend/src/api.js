async function 请求(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const msg = (data && data.detail) || text || r.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function qs(params = {}) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") usp.append(k, v);
  });
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  健康: () => 请求("/健康"),
  总览: () => 请求("/总览"),
  台参: () => 请求("/台参"),
  账户: () => 请求("/账户"),
  流水: (q = "") => 请求(`/流水${q}`),
  快照: (q = "") => 请求(`/快照${q}`),
  差额: (q = "") => 请求(`/差额${q}`),
  开放差额: (回放批次编号 = "") =>
    请求(`/开放差额${qs({ 回放批次编号 })}`),
  断档: () => 请求("/断档"),
  跳变: () => 请求("/跳变"),
  旁置: () => 请求("/旁置"),
  夹具: () => 请求("/夹具"),
  装载批次: () => 请求("/装载批次"),
  抽取批次: () => 请求("/抽取批次"),
  回放批次: () => 请求("/回放批次"),
  回放处置: (批号) => 请求(`/回放处置/${encodeURIComponent(批号)}`),
  工单: (仅待处理 = false) => 请求(`/工单?仅待处理=${仅待处理}`),
  盖写审计: () => 请求("/盖写审计"),
  工单闭环: (工单号, 修正余额 = null) =>
    请求("/工单闭环", { method: "POST", body: JSON.stringify({ 工单号, 修正余额 }) }),
  批量闭环: (payload) =>
    请求("/闭包处置", { method: "POST", body: JSON.stringify(payload || {}) }),
  闭包核证: (params = {}) => 请求(`/核证/差额闭包${qs(params)}`),
  闭包世代: (params = {}) => 请求(`/闭包世代${qs(params)}`),
  重开票列表: () => 请求("/闭包重开票"),
  重开审计: () => 请求("/闭包重开审计"),
  开重开票: (payload) =>
    请求("/闭包重开票", { method: "POST", body: JSON.stringify(payload || {}) }),
  装载夹具: (名) => 请求("/装载夹具", { method: "POST", body: JSON.stringify({ 名 }) }),
  抽取: (arg) => {
    const body = typeof arg === "string" ? { 营业日: arg } : arg || {};
    return 请求("/抽取", { method: "POST", body: JSON.stringify(body) });
  },
  回放: (arg) => {
    const body = typeof arg === "string" ? { 营业日: arg } : arg || {};
    return 请求("/回放", { method: "POST", body: JSON.stringify(body) });
  },
  重置: () => 请求("/重置业务库", { method: "POST", body: "{}" }),
};

// 兼容差额闭包页早期的命名导入。
export async function 取(path) {
  return 请求(path);
}
