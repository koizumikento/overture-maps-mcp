import assert from "node:assert/strict";
import { mkdir, readFile, copyFile, lstat, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = fileURLToPath(new URL("../", import.meta.url));
const manifest = JSON.parse(await readFile(new URL("../.openai/hosting.json", import.meta.url)));
assert(manifest.capabilities.includes("mcp"));
assert(!manifest.static && manifest.d1 === null && manifest.r2 === null);
// Reject stale files and links instead of packaging them or deleting unknown user files.
for (const [path, allowed] of [["dist", ["server", ".openai"]], ["dist/server", ["index.js"]], ["dist/.openai", ["hosting.json"]]]) {
  try {
    const stat = await lstat(`${root}/${path}`);
    assert(stat.isDirectory() && !stat.isSymbolicLink(), `Unsafe artifact directory: ${path}`);
    for (const entry of await readdir(`${root}/${path}`)) {
      assert(allowed.includes(entry), `Unexpected artifact file: ${path}/${entry}`);
      assert(!(await lstat(`${root}/${path}/${entry}`)).isSymbolicLink(), "Artifact links are forbidden");
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}
for (const path of ["dist/server", "dist/.openai"]) await mkdir(`${root}/${path}`, { recursive: true });
await copyFile(`${root}/worker/index.js`, `${root}/dist/server/index.js`);
await copyFile(`${root}/.openai/hosting.json`, `${root}/dist/.openai/hosting.json`);
const source = await readFile(`${root}/dist/server/index.js`);
const worker = await import(`data:text/javascript;base64,${source.toString("base64")}`);
assert.equal(typeof worker.default.fetch, "function");
if (process.argv.includes("--archive")) {
  assert(manifest.project_id, "Persist the Site project_id before packaging for deployment");
  const result = spawnSync("tar", ["-czf", "site.tar.gz", "-C", "dist", "."], { cwd: root, stdio: "inherit" });
  if (result.error) throw result.error;
  assert.equal(result.status, 0);
}
console.log("Sites Worker ESM artifact validated");
