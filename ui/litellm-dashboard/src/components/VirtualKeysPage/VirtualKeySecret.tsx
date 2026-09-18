import { useEffect, useRef, useState } from "react";
import { Copy, Eye, EyeOff } from "lucide-react";
import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import { Button } from "@/components/ui/button";
import { apiClient } from "@/components/networking";
import { extractProxyErrorMessage } from "@/lib/http/client";
import type { components } from "@/lib/http/schema";
import { toast } from "@/lib/toast";

export function VirtualKeySecret({ token }: { token: string }) {
  const { accessToken } = useAuthorized();
  return <SecretControl key={`${token}:${accessToken}`} token={token} accessToken={accessToken} />;
}

function SecretControl({ token, accessToken }: { token: string; accessToken: string | null }) {
  const [secret, setSecret] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const hide = () => {
      if (document.hidden) {
        request.current?.abort();
        setSecret(null);
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      request.current?.abort();
      document.removeEventListener("visibilitychange", hide);
    };
  }, [token, accessToken]);
  useEffect(() => {
    if (!secret) return;
    const timer = window.setTimeout(() => setSecret(null), 30000);
    return () => window.clearTimeout(timer);
  }, [secret]);
  const load = async (copy: boolean) => {
    if (!accessToken) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError(null);
    try {
      const value =
        secret ??
        (
          await apiClient.post<components["schemas"]["VirtualKeySecretResponse"]>("/key/reveal", {
            accessToken,
            body: { token },
            signal: controller.signal,
          })
        ).key;
      if (controller.signal.aborted) return;
      if (copy) {
        await navigator.clipboard.writeText(value);
        toast.success("密钥已复制");
      } else {
        setSecret(value);
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(extractProxyErrorMessage(failure));
    } finally {
      if (request.current === controller) setBusy(false);
    }
  };
  return (
    <div className="min-w-0 space-y-1" onClick={(event) => event.stopPropagation()}>
      <div className="flex items-center gap-1">
        <code className="max-w-72 select-all break-all text-xs" aria-label="虚拟密钥">
          {secret ?? "****************"}
        </code>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={busy || !accessToken}
          aria-label={secret ? "隐藏密钥" : "显示密钥"}
          title={secret ? "隐藏密钥" : "显示完整密钥，30 秒后自动隐藏"}
          onClick={() => (secret ? setSecret(null) : void load(false))}
        >
          {secret ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={busy || !accessToken}
          aria-label="复制密钥"
          title="复制完整密钥"
          onClick={() => void load(true)}
        >
          <Copy className="size-4" />
        </Button>
      </div>
      {error && (
        <p role="alert" className="max-w-md text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
