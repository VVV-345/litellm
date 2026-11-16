/** 本文件把号池供应商标识映射到仪表盘已有的内置厂商图标。 */

import { Logo } from "@/components/molecules/logo/Logo";
import { ProviderLogo } from "@/components/molecules/models/ProviderLogo";
import { cn } from "@/lib/cva.config";

import opencodeDark from "../../../../../public/assets/logos/opencode-dark.svg";
import opencodeLight from "../../../../../public/assets/logos/opencode-light.svg";
import zai from "../../../../../public/assets/logos/zai.svg";
import type { AccountPoolCreationSupplier } from "../../utils/AccountPoolTypes";

const providerBySupplier: Readonly<Record<Exclude<AccountPoolCreationSupplier, "glm" | "opencode">, string>> = {
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
}) => {
  if (supplier === "glm") {
    return <Logo src={zai.src} label="GLM" className={cn(className, "object-contain dark:invert")} />;
  }
  if (supplier === "opencode") {
    return (
      <>
        <Logo src={opencodeLight.src} label="OpenCode" className={cn(className, "object-contain dark:hidden")} />
        <Logo src={opencodeDark.src} label="OpenCode" className={cn(className, "hidden object-contain dark:block")} />
      </>
    );
  }
  const provider = providerBySupplier[supplier as keyof typeof providerBySupplier] ?? supplier;
  return <ProviderLogo provider={provider} className={className} />;
};
