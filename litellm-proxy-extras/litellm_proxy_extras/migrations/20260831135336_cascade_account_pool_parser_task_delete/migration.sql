-- 删除渠道时，解析任务应随渠道一并清理，避免目录删除被历史任务阻塞。

ALTER TABLE "LiteLLM_AccountPoolParserTask" DROP CONSTRAINT "LiteLLM_AccountPoolParserTask_channel_id_fkey";

ALTER TABLE "LiteLLM_AccountPoolParserTask"
ADD CONSTRAINT "LiteLLM_AccountPoolParserTask_channel_id_fkey"
FOREIGN KEY ("channel_id") REFERENCES "LiteLLM_AccountPoolChannel"("channel_id") ON DELETE CASCADE ON UPDATE CASCADE;
