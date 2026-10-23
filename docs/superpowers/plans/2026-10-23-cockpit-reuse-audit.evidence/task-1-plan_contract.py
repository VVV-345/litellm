import sys
from pathlib import Path
required=['Cockpit 整体替换执行计划', '目标架构：', '替换阶段 B：验证 Cockpit 服务化', '替换阶段 D：实现 Cockpit 后台接管', '替换阶段 E：行为兼容与差异验收', '额度耗尽换卡', '跨卡续聊', '回滚', '阶段 B 当前结果（2026-10-23）']
forbidden=['LiteLLM 号池是账号路由和重试的唯一 owner', '继续由 LiteLLM 网关执行']
s=Path(sys.argv[1]).read_text(encoding="utf-8-sig")
m=sum(x not in s for x in required);f=sum(x in s for x in forbidden)
print(f"missing={m} forbidden={f}")
sys.exit(int(bool(m or f)))
