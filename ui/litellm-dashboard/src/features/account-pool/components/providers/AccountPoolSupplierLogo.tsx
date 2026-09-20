/** 本文件把号池供应商标识映射到仪表盘已有的内置厂商图标。 */

import { ProviderLogo } from "@/components/molecules/models/ProviderLogo";

import type { AccountPoolSupplier } from "../../utils/AccountPoolTypes";

const providerBySupplier: Readonly<Record<AccountPoolSupplier, string>> = {
  openai_compatible: "openai",
  openai_codex: "openai",
  anthropic_claude: "anthropic",
  google_antigravity: "gemini",
  kimi: "moonshot",
  xai: "xai",
  gemini: "gemini",
  gemini_interactions: "gemini",
  vertex: "vertex_ai",
};

export const AccountPoolSupplierLogo = ({
  supplier,
  className = "size-5",
}: {
  supplier: string;
  className?: string;
}) => <ProviderLogo provider={providerBySupplier[supplier as AccountPoolSupplier] ?? supplier} className={className} />;
