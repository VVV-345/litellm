// 本机助手入口：配置固定来源与 Camoufox 路径，不随 Docker 服务启动。
import { randomBytes } from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { browserLauncher } from "./browser.mjs";
import { CamoufoxSessions } from "./core.mjs";
import { createHelperServer } from "./server.mjs";

const { values } = parseArgs({
  options: {
    origin: { type: "string", multiple: true },
    port: { type: "string", default: "10109" },
    executable: {
      type: "string",
      default: "/Applications/Camoufox.app/Contents/MacOS/camoufox",
    },
    "data-dir": {
      type: "string",
      default: join(homedir(), ".litellm-camoufox"),
    },
    "callback-port": { type: "string" },
    "show-key": { type: "boolean", default: false },
  },
});
try {
  const root = values["data-dir"];
  mkdirSync(root, { recursive: true, mode: 0o700 });
  chmodSync(root, 0o700);
  const keyFile = join(root, "helper.key");
  if (!existsSync(keyFile))
    writeFileSync(keyFile, randomBytes(32).toString("hex"), {
      mode: 0o600,
      flag: "wx",
    });
  chmodSync(keyFile, 0o600);
  const key = readFileSync(keyFile, "utf8").trim();
  if (!/^[a-f0-9]{64}$/.test(key)) throw new Error("Invalid helper key file");
  if (values["show-key"]) {
    process.stdout.write(key + "\n");
  } else {
    const origins = (values.origin ?? ["http://127.0.0.1:4001"]).map((raw) => {
      const url = new URL(raw);
      if (
        raw !== url.origin ||
        url.username ||
        url.password ||
        (url.protocol !== "https:" &&
          !(
            url.protocol === "http:" &&
            ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
          ))
      )
        throw new Error("Origin must be HTTPS or local HTTP");
      return url.origin;
    });
    const port = Number(values.port);
    const callbackPort = values["callback-port"]
      ? Number(values["callback-port"])
      : null;
    if (
      ![port, ...(callbackPort === null ? [] : [callbackPort])].every(
        (value) => Number.isInteger(value) && value > 0 && value <= 65535,
      )
    )
      throw new Error("Invalid port");
    if (!existsSync(values.executable))
      throw new Error("Camoufox executable not found");
    const sessions = new CamoufoxSessions({
      root,
      key,
      launch: browserLauncher(values.executable),
      callbackPort,
    });
    const server = createHelperServer({ origins, key, sessions });
    server.on("error", () => {
      console.error("Local helper could not bind its port");
      process.exitCode = 1;
    });
    server.listen(port, "127.0.0.1", () => {
      console.log(`Camoufox helper ready at http://127.0.0.1:${port}`);
      console.log(`Key file: ${keyFile}`);
      console.log(`Allowed dashboard origins: ${origins.join(", ")}`);
    });
    const close = async () => {
      server.closeAllConnections();
      server.close();
      await sessions.close();
    };
    process.once("SIGINT", () => {
      void close();
    });
    process.once("SIGTERM", () => {
      void close();
    });
  }
} catch (error) {
  console.error(
    error instanceof Error ? error.message : "Helper configuration failed",
  );
  process.exitCode = 1;
}
