import path from "path";
import { fileURLToPath } from "url";
import { randomUUID } from "node:crypto";

/** @type {import('next').NextConfig} */
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const nextConfig = {
  env: { NEXT_PUBLIC_UI_BUILD_ID: process.env.NEXT_PUBLIC_UI_BUILD_ID || randomUUID() },
  output: "export",
  experimental: { useTypeScriptCli: false },
  compiler: {
    removeConsole: process.env.NODE_ENV === "production" ? { exclude: ["error", "warn"] } : false,
  },
  // Required with output: "export" — default image optimizer runs only in server mode.
  // See https://nextjs.org/docs/messages/export-image-api
  images: {
    unoptimized: true,
  },
  basePath: "",
  assetPrefix: "/litellm-asset-prefix",
  trailingSlash: true,
  turbopack: {
    // Must be absolute; "." is no longer allowed
    root: __dirname,
  },
};

export default nextConfig;
