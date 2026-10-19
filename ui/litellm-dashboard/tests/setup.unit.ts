import { beforeEach, vi } from "vitest";

beforeEach(async () => {
  const { default: i18next } = await import("@/i18n");
  await i18next.changeLanguage("en");
});

vi.mock("@/lib/toast", () => ({
  toast: {
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    fromError: vi.fn(),
    dismiss: vi.fn(),
  },
}));
