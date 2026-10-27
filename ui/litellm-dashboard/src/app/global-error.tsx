"use client";

import { updateDashboard } from "@/components/DashboardRecovery";
import { canChunkReload } from "@/lib/dashboardRecovery";
import { useState } from "react";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  const [capped, setCapped] = useState(() => !canChunkReload());
  const reload = () => {
    updateDashboard();
    if (!canChunkReload()) setCapped(true);
  };
  return (
    <html lang="zh-CN">
      <body>
        <p>页面资源加载失败。更新会重新加载页面，未保存的配置需要确认后才会丢弃。</p>
        {error.digest ? <p>{error.digest}</p> : null}
        {capped ? (
          <p>已达到重载上限。请从新页面重新登录。</p>
        ) : (
          <button type="button" onClick={reload}>
            更新页面
          </button>
        )}
      </body>
    </html>
  );
}
