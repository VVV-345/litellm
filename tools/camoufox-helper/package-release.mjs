import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createPackage, PACKAGE_FILES } from "./manager-package.mjs";
import { runCommand } from "./manager-service.mjs";

try {
  const { values } = parseArgs({
    options: { out: { type: "string" }, help: { type: "boolean" } },
  });
  if (values.help) console.log("node package-release.mjs --out /完整/输出目录");
  else {
    if (!values.out) throw new Error("请用 --out 指定输出目录");
    const temporary = mkdtempSync(join(tmpdir(), "camoufox-release-"));
    try {
      const source = dirname(fileURLToPath(import.meta.url));
      const directory = join(temporary, "camoufox-helper");
      const { version } = createPackage(source, directory);
      const output = resolve(values.out);
      mkdirSync(output, { recursive: true });
      const zip = join(output, `camoufox-helper-macos-${version}.zip`);
      if (existsSync(zip) || existsSync(`${zip}.sha256`))
        throw new Error("输出文件已存在，请选择新目录");
      const result = runCommand(
        "/usr/bin/zip",
        [
          "-q",
          zip,
          ...[...PACKAGE_FILES, "SHA256SUMS.json"].map(
            (name) => `camoufox-helper/${name}`,
          ),
        ],
        { cwd: temporary },
      );
      if (result.status !== 0) throw new Error("ZIP 打包失败");
      const digest = createHash("sha256")
        .update(readFileSync(zip))
        .digest("hex");
      writeFileSync(
        `${zip}.sha256`,
        `${digest}  camoufox-helper-macos-${version}.zip\n`,
        { flag: "wx" },
      );
      console.log(zip);
      console.log(`${zip}.sha256`);
    } finally {
      rmSync(temporary, { recursive: true, force: true });
    }
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : "打包失败");
  process.exitCode = 1;
}
