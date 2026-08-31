"use client";

import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { CHINESE_LANGUAGE, ENGLISH_LANGUAGE, LANGUAGE_STORAGE_KEY } from "@/i18n";

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const nextLanguage = i18n.language === CHINESE_LANGUAGE ? ENGLISH_LANGUAGE : CHINESE_LANGUAGE;

  const switchLanguage = () => {
    void i18n.changeLanguage(nextLanguage);
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
  };

  return (
    <Button variant="outline" size="xs" onClick={switchLanguage} aria-label={t("language.switch")}>
      {nextLanguage === CHINESE_LANGUAGE ? t("language.chinese") : t("language.english")}
    </Button>
  );
}
