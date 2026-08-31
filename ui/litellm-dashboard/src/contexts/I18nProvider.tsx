"use client";

import { useEffect, type PropsWithChildren } from "react";
import { I18nextProvider } from "react-i18next";

import i18next, { CHINESE_LANGUAGE, ENGLISH_LANGUAGE, LANGUAGE_STORAGE_KEY } from "@/i18n";

const savedLanguage = (): string | null => {
  if (typeof window === "undefined") return null;
  const language = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return language === ENGLISH_LANGUAGE || language === CHINESE_LANGUAGE ? language : null;
};

export function I18nProvider({ children }: PropsWithChildren) {
  useEffect(() => {
    const language = savedLanguage();
    if (language && language !== i18next.language) void i18next.changeLanguage(language);

    const updateDocumentLanguage = (nextLanguage: string) => {
      document.documentElement.lang = nextLanguage;
    };
    updateDocumentLanguage(i18next.language);
    i18next.on("languageChanged", updateDocumentLanguage);

    return () => {
      i18next.off("languageChanged", updateDocumentLanguage);
    };
  }, []);

  return <I18nextProvider i18n={i18next}>{children}</I18nextProvider>;
}
