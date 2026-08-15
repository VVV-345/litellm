import type { TFunction } from "i18next";

export const translateUiText = (t: TFunction, value: string): string =>
  t(`ui.${value}`, { defaultValue: value });
