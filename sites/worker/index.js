const MAX_REQUEST = 256 * 1024;
const MAX_RESPONSE = 2 * 1024 * 1024;

async function boundedBody(stream, limit) {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw new Error("Body too large");
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function failure(status, message) {
  return new Response(message, { status, headers: { "Cache-Control": "no-store" } });
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/identity" && request.method === "GET") {
      const id = request.headers.get("oai-authenticated-user-id");
      return id ? Response.json({ id }, { headers: { "Cache-Control": "no-store" } }) : failure(401, "Sign in to the Site");
    }
    if (path !== "/mcp") return failure(404, "Not found");
    if (request.method !== "POST") return new Response("Use POST /mcp", { status: 405, headers: { "Allow": "POST", "Cache-Control": "no-store" } });
    // Trust these headers only behind Sites dispatch, never on a separately exposed Worker.
    const user = request.headers.get("oai-authenticated-user-id");
    if (!user) return failure(401, "Sign in to the Site");
    if (!env.OVERTURE_ALLOWED_USER_ID) return failure(503, "Configure Site access");
    if (user !== env.OVERTURE_ALLOWED_USER_ID) return failure(403, "Site access denied");
    let backend;
    try {
      backend = new URL(env.OVERTURE_BACKEND_URL);
      if (backend.protocol !== "https:" || backend.username || backend.password ||
          backend.pathname !== "/mcp" || backend.search || backend.hash ||
          !env.OVERTURE_BACKEND_TOKEN || env.OVERTURE_BACKEND_TOKEN.length < 32 ||
          /[^\x21-\x7e]/.test(env.OVERTURE_BACKEND_TOKEN)) throw new Error();
    } catch {
      return failure(503, "Configure the private backend");
    }
    if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !==
        "application/json") return failure(415, "Use application/json");
    let body;
    try {
      body = await boundedBody(request.body, MAX_REQUEST);
    } catch {
      return failure(413, "Reduce request size");
    }
    // MCP parsing, tool schemas, validation, and results belong to the existing Python SDK.
    const headers = new Headers({
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      "Authorization": `Bearer ${env.OVERTURE_BACKEND_TOKEN}`,
    });
    for (const [name, value] of request.headers) {
      if (["mcp-protocol-version", "mcp-method", "mcp-name"].includes(name) || name.startsWith("mcp-param-")) {
        headers.set(name, value);
      }
    }
    try {
      const upstream = await fetch(backend, {
        method: "POST", headers, body, redirect: "error", signal: AbortSignal.timeout(120000),
      });
      if (upstream.status === 202 || upstream.status === 204) {
        await upstream.body?.cancel();
        return new Response(null, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
      }
      if (![200, 400].includes(upstream.status) || !upstream.headers.get("content-type")?.startsWith("application/json")) {
        await upstream.body?.cancel();
        return failure(502, "Backend unavailable; check service configuration");
      }
      const response = await boundedBody(upstream.body, MAX_RESPONSE);
      return new Response(response, {
        status: upstream.status,
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      });
    } catch {
      return failure(502, "Backend unavailable; retry later");
    }
  },
};
