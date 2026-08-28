-- Releases a lease and returns any unsettled quota reservation.
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[3]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then
  redis.call('ZREM', KEYS[2], ARGV[2])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return 0
end
if redis.call('HGET', KEYS[1], 'released') == '1' then return 1 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local probe_key = redis.call('HGET', KEYS[1], 'probe_key')
local settled = redis.call('HGET', KEYS[1], 'settled')
local quota_count = tonumber(redis.call('HGET', KEYS[1], 'quota_count') or '0')
for quota_index = 1, quota_count do
  local window_key = redis.call('HGET', KEYS[1], 'quota_window_' .. quota_index)
  local usage_key = redis.call('HGET', KEYS[1], 'quota_usage_' .. quota_index)
  local amount = redis.call('HGET', KEYS[1], 'quota_amount_' .. quota_index) or '0'
  local quota_probe_key = redis.call('HGET', KEYS[1], 'quota_probe_' .. quota_index)
  if quota_probe_key and quota_probe_key ~= '' and redis.call('GET', quota_probe_key) == lease_id then
    redis.call('DEL', quota_probe_key)
  end
  if window_key then redis.call('ZREM', window_key .. ':reservations', lease_id) end
  if window_key then redis.call('ZREM', window_key .. ':absolute_reservations', lease_id) end
  if settled ~= '1' and window_key and usage_key and redis.call('EXISTS', window_key) == 1 then
    local reserved = redis.call('HGET', window_key, 'reserved_units') or '0'
    local updated_reserved = '0'
    if quota_compare_unsigned(reserved, amount) ~= -1 then
      updated_reserved = quota_subtract_unsigned(reserved, amount)
    end
    redis.call('HSET', window_key, 'reserved_units', updated_reserved)
    quota_refresh_rolling(window_key, usage_key, tonumber(ARGV[4]), ARGV[5])
  end
end
local inflight_key = ARGV[1] .. account_id .. ':inflight'
local inflight = tonumber(redis.call('GET', inflight_key) or '0')
if inflight > 0 then redis.call('DECR', inflight_key) end
if probe_key and probe_key ~= '' and redis.call('GET', probe_key) == lease_id then redis.call('DEL', probe_key) end
redis.call('HSET', KEYS[1], 'released', '1')
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
