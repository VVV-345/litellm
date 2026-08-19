// 本文件提供 LiteLLM Dashboard 中 Account Pool 渠道解析管理页的路由入口。
"use client";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";

import AccountPoolPage from "./_components/AccountPoolPage";

export default function AccountPoolRoute() {
  const authorization = useAuthorized();
  return <AccountPoolPage accessToken={authorization.accessToken} userRole={authorization.userRole} />;
}
