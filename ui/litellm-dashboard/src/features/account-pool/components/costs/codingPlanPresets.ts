/** 国内编程订阅的官方价格快照，仅供人工录入费用时参考。 */
export const codingPlansVerifiedOn = "2026-11-18";
type PlanPrice = Readonly<{
  label: string;
  amountCents: number | null;
  cycle: "月付" | "季付" | "年付";
}>;
type CodingPlanProvider = Readonly<{
  id: string;
  name: string;
  source: string;
  note: string;
  plans: readonly PlanPrice[];
}>;

export const codingPlanProviders: readonly CodingPlanProvider[] = [
  {
    id: "glm",
    name: "智谱 GLM Coding Plan",
    source: "https://bigmodel.cn/glm-coding",
    note: "连续包月价格。包季、包年及其他优惠以下单页为准",
    plans: [
      { label: "Lite", amountCents: 11800, cycle: "月付" },
      { label: "Pro", amountCents: 53800, cycle: "月付" },
      { label: "Max", amountCents: 107800, cycle: "月付" },
    ],
  },
  {
    id: "minimax",
    name: "MiniMax M Plan",
    source: "https://www.minimax.cn/m-plan",
    note: "当前套餐为 Go / Explore / Build。年付金额为一次支付的全年总额",
    plans: [
      { label: "Go", amountCents: 4900, cycle: "月付" },
      { label: "Explore", amountCents: 11900, cycle: "月付" },
      { label: "Build", amountCents: 46900, cycle: "月付" },
      { label: "Go", amountCents: 49000, cycle: "年付" },
      { label: "Explore", amountCents: 119000, cycle: "年付" },
      { label: "Build", amountCents: 469000, cycle: "年付" },
    ],
  },
  {
    id: "kimi",
    name: "Kimi 会员（含 Code）",
    source: "https://www.kimi.com/help/membership/membership-pricing",
    note: "新套餐 Plus 及以上包含 Kimi Code，Go 不包含。年付按全年总额录入",
    plans: [
      { label: "Plus", amountCents: 9900, cycle: "月付" },
      { label: "Pro", amountCents: 19900, cycle: "月付" },
      { label: "Max", amountCents: 69900, cycle: "月付" },
      { label: "Ultra", amountCents: 139900, cycle: "月付" },
      { label: "Plus", amountCents: 94800, cycle: "年付" },
      { label: "Pro", amountCents: 190800, cycle: "年付" },
      { label: "Max", amountCents: 670800, cycle: "年付" },
      { label: "Ultra", amountCents: 1342800, cycle: "年付" },
    ],
  },
  {
    id: "aliyun",
    name: "阿里云百炼 Token Plan",
    source: "https://help.aliyun.com/zh/model-studio/token-plan-personal-overview",
    note: "常规月价。Lite / Essential 新客限时价为 ¥19.50 / ¥39.50，有效至 2026-11-30，需按实际付款改金额",
    plans: [
      { label: "Lite", amountCents: 3900, cycle: "月付" },
      { label: "Essential", amountCents: 7900, cycle: "月付" },
      { label: "Standard", amountCents: 13900, cycle: "月付" },
      { label: "Pro", amountCents: 31200, cycle: "月付" },
    ],
  },
  {
    id: "aliyun-coding",
    name: "阿里云百炼 Coding Plan（旧版）",
    source: "https://help.aliyun.com/zh/model-studio/coding-plan",
    note: "Pro 目录价 ¥200/月；官方列有新客首月 ¥39.90，资格以下单页为准。Lite 已停止新购与续费",
    plans: [{ label: "Pro", amountCents: 20000, cycle: "月付" }],
  },
  {
    id: "volcengine",
    name: "火山方舟 Coding Plan",
    source: "https://www.volcengine.com/activity/codingplan",
    note: "官方确认 Lite / Pro 两档，但当前价格接口未返回报价。仅预填名称，请按订单手填金额",
    plans: [
      { label: "Lite", amountCents: null, cycle: "月付" },
      { label: "Pro", amountCents: null, cycle: "月付" },
    ],
  },
  {
    id: "tencent",
    name: "腾讯云 Coding Plan",
    source: "https://cloud.tencent.com/document/product/1772/128947",
    note: "Lite / Pro 原价。早期首购活动已结束，当前优惠以下单页为准",
    plans: [
      { label: "Lite", amountCents: 4000, cycle: "月付" },
      { label: "Pro", amountCents: 20000, cycle: "月付" },
    ],
  },
  {
    id: "baidu",
    name: "百度千帆 Token Plan（积分制）",
    source: "https://cloud.baidu.com/product/qianfan_home/token_plan_personal.html",
    note: "官网当前展示价，含双十一、首月等活动，不能作为续费原价。请核对购买资格与实付金额",
    plans: [
      { label: "Mini · 活动展示价", amountCents: 490, cycle: "月付" },
      { label: "Lite · 活动展示价", amountCents: 1990, cycle: "月付" },
      { label: "Standard · 展示价", amountCents: 4990, cycle: "月付" },
      { label: "Pro · 活动展示价", amountCents: 9990, cycle: "月付" },
      { label: "Max · 活动展示价", amountCents: 29990, cycle: "月付" },
    ],
  },
  {
    id: "mimo",
    name: "小米 MiMo Token Plan",
    source: "https://mimo.mi.com/docs/zh-CN/tokenplan/Token%20Plan/subscription",
    note: "采用官网人民币定价，不换算美元。年付金额为一次支付的全年总额",
    plans: [
      { label: "Lite", amountCents: 3900, cycle: "月付" },
      { label: "Standard", amountCents: 9900, cycle: "月付" },
      { label: "Pro", amountCents: 32900, cycle: "月付" },
      { label: "Max", amountCents: 65900, cycle: "月付" },
      { label: "Lite", amountCents: 40800, cycle: "年付" },
      { label: "Standard", amountCents: 104400, cycle: "年付" },
      { label: "Pro", amountCents: 346800, cycle: "年付" },
      { label: "Max", amountCents: 694800, cycle: "年付" },
    ],
  },
  {
    id: "huawei",
    name: "华为云 MaaS Token Plan",
    source: "https://support.huaweicloud.com/price-maas/price-maas-0035.html",
    note: "个人版常规月价，每日限量发售，实际购买资格以控制台为准",
    plans: [
      { label: "Lite", amountCents: 5900, cycle: "月付" },
      { label: "Standard", amountCents: 14900, cycle: "月付" },
      { label: "Pro", amountCents: 39900, cycle: "月付" },
      { label: "Max", amountCents: 79900, cycle: "月付" },
    ],
  },
  {
    id: "xfyun",
    name: "讯飞星辰 Astron Coding Plan",
    source: "https://static.xfyun.cn/doc/spark/CodingPlan.html",
    note: "无忧版、专业版已下线。速通版官方另列限时首月 ¥699、首季 ¥2697，按实际付款核对",
    plans: [
      { label: "高效版", amountCents: 19900, cycle: "月付" },
      { label: "速通版", amountCents: 99900, cycle: "月付" },
      { label: "高效版", amountCents: 53800, cycle: "季付" },
      { label: "速通版", amountCents: 299700, cycle: "季付" },
    ],
  },
];
