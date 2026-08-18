// 本文件装配 Admin UI 的全局上下文，并使用本地系统字体避免构建时访问外部字体服务。
import type { Metadata } from "next";
import "./globals.css";

import { NuqsAdapter } from "nuqs/adapters/next/app";

import AntdGlobalProvider from "@/contexts/AntdGlobalProvider";
import { AuthProvider } from "@/contexts/AuthContext";
import { I18nProvider } from "@/contexts/I18nProvider";
import ReactQueryProvider from "@/contexts/ReactQueryProvider";

export const metadata: Metadata = {
  title: "LiteLLM Dashboard",
  description: "LiteLLM Proxy Admin UI",
  icons: { icon: "/get_favicon" },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <NuqsAdapter>
          <ReactQueryProvider>
            <I18nProvider>
              <AntdGlobalProvider>
                <AuthProvider>{children}</AuthProvider>
              </AntdGlobalProvider>
            </I18nProvider>
          </ReactQueryProvider>
        </NuqsAdapter>
      </body>
    </html>
  );
}
