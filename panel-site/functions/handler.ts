// 探针：回答"这台机器上的进程能不能写进站点函数"。无状态、无凭据、不落任何数据。
const 头 = { "Cache-Control": "no-store" };
const 上限字节 = 4096;

function 是探针(路径: string) {
  return /(^|\/)probe\/?$/.test(路径);
}

async function 处理(request: Request): Promise<Response> {
  const 路径 = new URL(request.url).pathname;
  if (!是探针(路径)) {
    return Response.json({ ok: false, error: "not_found" }, { status: 404, headers: 头 });
  }

  // 网关会剥掉浏览器自带的 x-qoder-*，只由它自己注入身份；这里只报"有没有"，不报值。
  const 观察 = {
    method: request.method,
    path: 路径,
    has_origin: request.headers.has("origin"),
    has_referer: request.headers.has("referer"),
    has_user_context: request.headers.has("x-qoder-user-context"),
    content_type: request.headers.get("content-type") ?? "",
    ua: (request.headers.get("user-agent") ?? "").slice(0, 40),
  };

  if (request.method === "GET") {
    return Response.json({ ok: true, mode: "get", 观察 }, { headers: 头 });
  }

  if (request.method !== "POST") {
    return Response.json(
      { ok: false, error: "method_not_allowed" },
      { status: 405, headers: { ...头, Allow: "GET, POST" } },
    );
  }

  const 文本 = await request.text();
  if (文本.length > 上限字节) {
    return Response.json({ ok: false, error: "body_too_large" }, { status: 413, headers: 头 });
  }
  let 收到: unknown = null;
  if (文本) {
    try {
      收到 = JSON.parse(文本);
    } catch {
      return Response.json({ ok: false, error: "invalid_json" }, { status: 400, headers: 头 });
    }
  }
  // 写这条路能不能通，就看这里：收到过 body 说明网关没把 POST 拦在门外。
  return Response.json(
    { ok: true, mode: "post", 收到, 字节: 文本.length, 观察 },
    { headers: 头 },
  );
}

export async function handler(request: Request): Promise<Response> {
  try {
    return await 处理(request);
  } catch {
    return Response.json({ ok: false, error: "handler_failed" }, { status: 500, headers: 头 });
  }
}
