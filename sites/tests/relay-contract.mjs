import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import worker from "../worker/index.js";

const env = { OVERTURE_ALLOWED_USER_ID: "fixture-owner", OVERTURE_BACKEND_URL: "https://backend.example/mcp", OVERTURE_BACKEND_TOKEN: "synthetic-service-secret-32-characters" };
const fixtures = JSON.parse(await readFile(process.argv[2], "utf8"));
for (const fixture of fixtures) {
  const bytes = JSON.stringify(fixture.response);
  globalThis.fetch = async (url, options) => {
    assert.equal(url.href, env.OVERTURE_BACKEND_URL);
    assert.equal(options.redirect, "manual");
    assert.deepEqual(JSON.parse(new TextDecoder().decode(options.body)), fixture.request);
    return new Response(bytes, { headers: { "Content-Type": "application/json" } });
  };
  const response = await worker.fetch(new Request("https://site.example/mcp", {
    method: "POST", headers: { "Content-Type": "application/json", "oai-authenticated-user-id": "fixture-owner", "MCP-Protocol-Version": fixture.protocol ?? "2025-11-25", "Mcp-Method": fixture.request.method, ...(fixture.request.params?.name ? { "Mcp-Name": fixture.request.params.name } : {}) },
    body: JSON.stringify(fixture.request),
  }), env);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), bytes);
}
console.log(`Worker preserved ${fixtures.length} real SDK fixture envelopes`);
