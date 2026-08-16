"use client";

import React, { useEffect, useRef, useState } from "react";
import { ConfigProvider, notification, message } from "antd";
import { StyleProvider } from "@ant-design/cssinjs";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import i18next, { CHINESE_LANGUAGE } from "@/i18n";
import { setNotificationInstance } from "@/components/molecules/notifications_manager";
import { setMessageInstance } from "@/components/molecules/message_manager";

export default function AntdGlobalProvider({ children }: { children: React.ReactNode }) {
  const [notificationApi, notificationContextHolder] = notification.useNotification();
  const [messageApi, messageContextHolder] = message.useMessage();
  const initialized = useRef(false);
  const [antdLocale, setAntdLocale] = useState(() =>
    i18next.language === CHINESE_LANGUAGE ? zhCN : enUS,
  );

  useEffect(() => {
    if (!initialized.current) {
      setNotificationInstance(notificationApi);
      setMessageInstance(messageApi);
      initialized.current = true;
    }
  }, [notificationApi, messageApi]);

  useEffect(() => {
    const updateLocale = (nextLanguage: string) => {
      setAntdLocale(nextLanguage === CHINESE_LANGUAGE ? zhCN : enUS);
    };
    i18next.on("languageChanged", updateLocale);
    return () => {
      i18next.off("languageChanged", updateLocale);
    };
  }, []);

  return (
    <StyleProvider layer>
      <ConfigProvider theme={{ cssVar: true }} locale={antdLocale}>
        {notificationContextHolder}
        {messageContextHolder}
        {children}
      </ConfigProvider>
    </StyleProvider>
  );
}
