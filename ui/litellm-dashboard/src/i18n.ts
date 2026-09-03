import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";

export const LANGUAGE_STORAGE_KEY = "litellm-ui-language";
export const ENGLISH_LANGUAGE = "en";
export const CHINESE_LANGUAGE = "zh-CN";

const i18nextConfig = {
  resources: {
    en: { translation: en },
    "zh-CN": { translation: zhCN },
  },
  lng: CHINESE_LANGUAGE,
  fallbackLng: ENGLISH_LANGUAGE,
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
  nsSeparator: false as const,
};

void i18next.use(initReactI18next).init(i18nextConfig);

export default i18next;
