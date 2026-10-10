import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "../worker/index.js";

const env = { OVERTURE_ALLOWED_USER_ID: "owner", OVERTURE_BACKEND_URL: "https://backend.example/mcp", OVERTURE_BACKEND_TOKEN: "synthetic-service-secret-32-characters" };
const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" });
function request(headers = {}, data = body) {
  return new Request("https://site.example/mcp", { method: "POST", body: data, headers: { "Content-Type": "application/json", "oai-authenticated-user-id": "owner", ...headers } });
}

test("identity and configuration deny before contacting backend", async () => {
  globalThis.fetch = () => { throw new Error("must not contact backend"); };
  assert.equal((await worker.fetch(request({ "oai-authenticated-user-id": "" }), env)).status, 401);
  assert.equal((await worker.fetch(request({ "oai-authenticated-user-id": "other" }), env)).status, 403);
  assert.equal((await worker.fetch(request(), { ...env, OVERTURE_ALLOWED_USER_ID: "" })).status, 503);
  for (const url of ["http://backend.example/mcp", "https://backend.example/other", "https://user:pass@backend.example/mcp", "https://backend.example/mcp?url=evil"]) {
    assert.equal((await worker.fetch(request(), { ...env, OVERTURE_BACKEND_URL: url })).status, 503);
  }
  assert.equal((await worker.fetch(request({ "Content-Type": "text/plain" }), env)).status, 415);
  assert.equal((await worker.fetch(request({}, "x".repeat(256 * 1024 + 1)), env)).status, 413);
  assert.equal((await worker.fetch(new Request("https://site.example/mcp"), env)).status, 405);
  assert.equal((await worker.fetch(new Request("https://site.example/identity"), env)).status, 401);
  const identity = await worker.fetch(new Request("https://site.example/identity", { headers: { "oai-authenticated-user-id": "owner" } }), env);
  assert.deepEqual(await identity.json(), { id: "owner" });
  assert.equal(identity.headers.get("Cache-Control"), "no-store");
});

test("MCP ingress permits missing or same Origin and rejects foreign Origin before fetch", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    return Response.json({ jsonrpc: "2.0", id: 1, result: { tools: [] } });
  };
  for (const headers of [{}, { Origin: "https://site.example" }]) {
    assert.equal((await worker.fetch(request(headers), env)).status, 200);
  }
  assert.equal(calls, 2);
  for (const origin of ["https://foreign.example", "https://site.example.evil", "http://site.example", "https://site.example:444", "null", ""]) {
    const response = await worker.fetch(request({ Origin: origin }), env);
    assert.equal(response.status, 403);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(calls, 2);
  }
});

test("fixed endpoint, protocol headers and bytes survive; visitor credentials do not", async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url.href, env.OVERTURE_BACKEND_URL);
    assert.equal(options.headers.get("Authorization"), `Bearer ${env.OVERTURE_BACKEND_TOKEN}`);
    assert.equal(options.headers.get("MCP-Protocol-Version"), "2026-07-28");
    assert.equal(options.headers.get("Mcp-Method"), "tools/list");
    assert.equal(options.headers.get("Mcp-Name"), "overture_catalog");
    assert.equal(options.headers.get("Mcp-Param-test"), "value");
    for (const header of ["Cookie", "Origin", "OAI-Sites-Authorization", "oai-authenticated-user-id", "Mcp-Session-Id"]) assert.equal(options.headers.get(header), null);
    assert.equal(options.redirect, "error");
    assert.equal(new TextDecoder().decode(options.body), body);
    return new Response('{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}', { headers: { "Content-Type": "application/json", "Set-Cookie": "secret" } });
  };
  const response = await worker.fetch(request({ "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list", "Mcp-Name": "overture_catalog", "Mcp-Param-test": "value", Authorization: "Bearer visitor", Cookie: "secret", "OAI-Sites-Authorization": "Bearer bypass", Origin: "https://site.example" }), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Set-Cookie"), null);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("notifications, transport errors, redirects and size limits", async () => {
  for (const status of [202, 204]) {
    globalThis.fetch = async () => new Response(null, { status });
    assert.equal((await worker.fetch(request(), env)).status, status);
  }
  for (const response of [new Response("private traceback", { status: 500 }), new Response('{"secret":"private traceback"}', { status: 500, headers: { "Content-Type": "application/json" } }), new Response(null, { status: 302, headers: { Location: "https://evil.test" } }), new Response("x".repeat(2 * 1024 * 1024 + 1), { headers: { "Content-Type": "application/json" } })]) {
    globalThis.fetch = async () => response;
    const result = await worker.fetch(request(), env);
    assert.equal(result.status, 502);
    assert(! (await result.text()).includes("private traceback"));
  }
  globalThis.fetch = async () => { throw new Error("secret backend details"); };
  assert.equal((await worker.fetch(request(), env)).status, 502);
});
