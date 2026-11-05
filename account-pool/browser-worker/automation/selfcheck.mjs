// 在无网络临时容器内验证真实浏览器、noVNC 与服务端输入门禁，不触碰账号。
import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { promisify } from "node:util";
import { firefox } from "playwright-core";

const exec = promisify(execFile);
const started = performance.now();
async function main() {
  const options = JSON.parse(execFileSync("python", ["/app/camoufox_profile.py"], { encoding: "utf8", maxBuffer: 2_000_000 }));
  const browser = await firefox.launch(options);
  try {
    const page = await browser.newPage({ viewport: null });
    await page.goto("data:text/html,<h1>Isolated browser self check</h1>");
    assert.equal(await page.locator("h1").textContent(), "Isolated browser self check");
    const geometry = await page.evaluate(() => ({
      width: innerWidth, height: innerHeight, outerWidth, outerHeight, x: screenX, y: screenY,
      screenWidth: screen.width, screenHeight: screen.height, scale: devicePixelRatio,
    }));
    assert.ok(geometry.width > 0 && geometry.height > 0);
    assert.ok(geometry.outerWidth >= geometry.width && geometry.outerHeight > geometry.height);
    assert.equal(geometry.x, 0);
    assert.equal(geometry.y, 0);
    assert.ok((geometry.x + geometry.outerWidth) * geometry.scale <= 1920);
    assert.ok((geometry.y + geometry.outerHeight) * geometry.scale <= 1080);
    assert.equal((await fetch("http://127.0.0.1:8093/vnc.html")).status, 200);
    for (const [command, expected] of [["viewonly", "1"], ["noviewonly", "0"], ["viewonly", "1"]]) {
      await exec("x11vnc", ["-display", ":99", "-sync", "-R", command], { timeout: 5000 });
      const query = await exec("x11vnc", ["-display", ":99", "-Q", "viewonly"], { timeout: 5000 });
      assert.match(query.stdout, new RegExp(`viewonly:${expected}`));
    }
    process.stdout.write(JSON.stringify({ healthy: true, sdk: "0.5.9", browser: browser.version(), elapsed_ms: Math.round(performance.now() - started) }) + "\n");
  } finally {
    await browser.close();
  }
}
main().catch(() => {
  process.stderr.write("Camoufox display, proxy isolation or input-gate self check failed\n");
  process.exitCode = 1;
});
