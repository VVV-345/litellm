/** 费用表单的金额、日期和分页计算，不承担财务分摊。 */
export const costLabels = { subscription: "账号订阅", proxy: "代理", server: "服务器" } as const;
export const money = (cents: number) =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(cents / 100);
const chinaDateOptions: Intl.DateTimeFormatOptions = {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
};
export const chinaToday = () => new Intl.DateTimeFormat("en-CA", chinaDateOptions).format(new Date());
export function parseCents(value: string): number | null {
  if (!/^\d+(\.\d{1,2})?$/.test(value.trim())) return null;
  const [whole, fraction = ""] = value.trim().split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return Number.isSafeInteger(cents) && cents > 0 && cents <= 1_000_000_000_000 ? cents : null;
}
export function nextDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}
export function expiryLabel(days: number | null | undefined): string {
  if (days === null || days === undefined) return "未录入";
  if (days < 0) return `已到期 ${-days} 天`;
  if (days === 0) return "今天到期";
  return `${days} 天后到期`;
}
