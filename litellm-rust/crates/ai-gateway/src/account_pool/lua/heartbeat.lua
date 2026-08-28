-- Extends a lease without crossing its absolute deadline.
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[3]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then return 0 end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 0 end
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
local absolute_expires_at = tonumber(redis.call('HGET', KEYS[1], 'absolute_expires_at') or '')
local now = tonumber(ARGV[4])
if not absolute_expires_at or not now or now >= absolute_expires_at then return 0 end
local expires_at = math.min(tonumber(ARGV[1]), absolute_expires_at)
local lease_ttl = math.ceil(expires_at - now)
if lease_ttl <= 0 then return 0 end
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) ~= lease_id then return 0 end
if probe_key and probe_key ~= '' then redis.call('EXPIRE', probe_key, lease_ttl) end
local quota_count = tonumber(redis.call('HGET', KEYS[1], 'quota_count') or '0')
for quota_index = 1, quota_count do
  local window_key = redis.call('HGET', KEYS[1], 'quota_window_' .. quota_index)
  local quota_probe_key = redis.call('HGET', KEYS[1], 'quota_probe_' .. quota_index)
  if quota_probe_key and quota_probe_key ~= '' then
    if redis.call('GET', quota_probe_key) ~= lease_id then return 0 end
    redis.call('EXPIRE', quota_probe_key, lease_ttl)
  end
  if window_key then redis.call('ZADD', window_key .. ':reservations', expires_at, lease_id) end
end
redis.call('HSET', KEYS[1], 'expires_at', expires_at)
redis.call('ZADD', KEYS[2], expires_at, ARGV[2])
return 1
