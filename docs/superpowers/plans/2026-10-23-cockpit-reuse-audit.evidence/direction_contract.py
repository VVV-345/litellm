"""检查计划是否记载策略管理要求，不代表运行时能力已实现。"""
import sys
from pathlib import Path
REQUIRED = ['管理员可配置策略执行约定（2026-10-23）', '候选范围只能缩小，不能扩大主体授权范围', '重试次数不包含首次请求', '已保存、待生效、已生效', '不支持的选项禁止保存', '不得提供绕过安全边界的设置', '两个管理界面必须读写同一份版本化策略']
s = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
missing = sum(marker not in s for marker in REQUIRED)
print(f"direction_missing={missing}")
sys.exit(int(missing > 0))
