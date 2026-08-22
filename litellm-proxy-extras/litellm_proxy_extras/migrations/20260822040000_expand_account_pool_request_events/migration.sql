-- 本迁移扩展系统运行事实来源，使请求调度与租约生命周期进入统一事件日志。

ALTER TABLE "LiteLLM_AccountPoolOperationalEvent"
    DROP CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_source_check";

ALTER TABLE "LiteLLM_AccountPoolOperationalEvent"
    ADD CONSTRAINT "LiteLLM_AccountPoolOperationalEvent_source_check"
    CHECK ("source" IN ('parser_task', 'parser_snapshot_export', 'sync_reconcile', 'request_lifecycle'));
