-- Replaces reservations with trusted usage and updates health atomically.
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local lease_generation = redis.call('HGET', KEYS[1], 'generation_id') or ''
local runtime_generation = redis.call('GET', KEYS[7]) or ''
if lease_generation ~= '' and runtime_generation ~= lease_generation then return 0 end
if redis.call('HGET', KEYS[1], 'settled') == '1' then return 2 end
local account_id = redis.call('HGET', KEYS[1], 'account_id')
local lease_id = redis.call('HGET', KEYS[1], 'lease_id')
local state_key = ARGV[1] .. account_id .. ':state'
local action = ARGV[2]
local consumption = tonumber(ARGV[3] or '0')
local cooldown_until = tonumber(ARGV[4] or '0')
local reason_code = ARGV[5]
local exclusion_scope = ARGV[6]
local exclusion_source = ARGV[7]
local starts_at = ARGV[8]
local retry_at = ARGV[9]
local quota_count = tonumber(redis.call('HGET', KEYS[1], 'quota_count') or '0')
local quota_states = {}
for quota_index = 1, quota_count do
  local window_key = redis.call('HGET', KEYS[1], 'quota_window_' .. quota_index)
  local usage_key = redis.call('HGET', KEYS[1], 'quota_usage_' .. quota_index)
  local amount = redis.call('HGET', KEYS[1], 'quota_amount_' .. quota_index) or '0'
  local confirms_reset = redis.call('HGET', KEYS[1], 'quota_confirms_' .. quota_index) or '0'
  local quota_probe_key = redis.call('HGET', KEYS[1], 'quota_probe_' .. quota_index) or ''
  if not window_key or not usage_key then return 0 end
  local remaining, quota_error = quota_refresh_rolling(window_key, usage_key, tonumber(ARGV[13]), ARGV[15])
  if quota_error then return 0 end
  local reserved = redis.call('HGET', window_key, 'reserved_units') or '0'
  if quota_compare_unsigned(reserved, amount) == -1 then return 0 end
  local kind = redis.call('HGET', window_key, 'kind') or ''
  local actual = ''
  if kind == 'requests' then actual = ARGV[10] end
  if kind == 'tokens' then actual = ARGV[11] end
  if kind == 'currency' then actual = ARGV[12] end
  if actual ~= '' and not quota_normalize_unsigned(actual) then return 0 end
  quota_states[quota_index] = {
    window_key = window_key,
    usage_key = usage_key,
    reserved = reserved,
    amount = amount,
    remaining = remaining,
    actual = actual,
    confirms_reset = confirms_reset,
    probe_key = quota_probe_key
  }
end
for _, quota_state in ipairs(quota_states) do
  local updated_reserved = quota_subtract_unsigned(quota_state.reserved, quota_state.amount)
  redis.call('HSET', quota_state.window_key, 'reserved_units', updated_reserved)
  redis.call('ZREM', quota_state.window_key .. ':reservations', lease_id)
  redis.call('ZREM', quota_state.window_key .. ':absolute_reservations', lease_id)
  local remaining = quota_state.remaining
  if quota_state.confirms_reset == '1' and ARGV[14] == '1' then
    remaining = redis.call('HGET', quota_state.window_key, 'limit_units') or ''
    local next_retry = quota_next_fixed_retry(quota_state.window_key, tonumber(ARGV[13]))
    redis.call('HSET', quota_state.window_key, 'remaining_units', remaining, 'retry_at', next_retry)
  end
  if quota_state.actual ~= '' and remaining ~= '' then
    if quota_state.actual ~= '0' then
      redis.call('ZADD', quota_state.usage_key, ARGV[13], quota_state.actual .. '|' .. lease_id)
    end
    if redis.call('HGET', quota_state.window_key, 'window_type') == 'rolling' then
      quota_refresh_rolling(quota_state.window_key, quota_state.usage_key, tonumber(ARGV[13]), ARGV[15])
    elseif quota_compare_unsigned(remaining, quota_state.actual) == -1 then
      redis.call('HSET', quota_state.window_key, 'remaining_units', '0')
    else
      redis.call('HSET', quota_state.window_key, 'remaining_units', quota_subtract_unsigned(remaining, quota_state.actual))
    end
  end
  if quota_state.probe_key ~= '' and redis.call('GET', quota_state.probe_key) == lease_id then
    redis.call('DEL', quota_state.probe_key)
  end
end
if consumption > 0 then
  for _, field in ipairs({'quota_total', 'quota_five_hour', 'quota_weekly'}) do
    local value = redis.call('HGET', state_key, field)
    if value and value ~= '' then
      redis.call('HSET', state_key, field, math.max(0, tonumber(value) - consumption))
    end
  end
end
local write_exclusion = false
if action == 'success' then
  redis.call('HSET', state_key, 'health', 'healthy', 'consecutive_failures', '0', 'cooldown_until', '0', 'reason_code', '')
  for key_index = 2, 5 do redis.call('DEL', KEYS[key_index]) end
elseif action == 'disable' then
  redis.call('HSET', state_key, 'health', 'unhealthy', 'reason_code', reason_code)
  write_exclusion = true
elseif action == 'observe' then
  write_exclusion = exclusion_scope ~= ''
elseif action == 'cooldown' then
  write_exclusion = true
else
  local failures = redis.call('HINCRBY', state_key, 'consecutive_failures', 1)
  if failures >= 3 then
    redis.call('HSET', state_key, 'health', 'cooldown', 'cooldown_until', cooldown_until, 'reason_code', reason_code)
    write_exclusion = true
  else
    redis.call('HSET', state_key, 'health', 'degraded', 'reason_code', reason_code)
  end
end
if write_exclusion and exclusion_scope ~= '' then
  local target_key = KEYS[4]
  if exclusion_scope == 'channel' then target_key = KEYS[2] end
  if exclusion_scope == 'model' then target_key = KEYS[3] end
  if exclusion_scope == 'billing_route' then target_key = KEYS[5] end
  redis.call('HSET', target_key, exclusion_source .. '|' .. reason_code, starts_at .. '|' .. retry_at)
end
local latency = tonumber(ARGV[16] or '0')
local probe = redis.call('HGET', KEYS[1], 'probe') == '1'
if action == 'success' and latency > 0 and not probe then
  local current = tonumber(redis.call('HGET', KEYS[6], 'ewma_ms') or '')
  local count = tonumber(redis.call('HGET', KEYS[6], 'sample_count') or '0')
  local alpha = tonumber(ARGV[17])
  local ewma = current and (alpha * latency + (1 - alpha) * current) or latency
  redis.call('HSET', KEYS[6], 'deployment_id', redis.call('HGET', KEYS[1], 'deployment_id'),
    'ewma_ms', ewma, 'sample_count', count + 1, 'observed_at', ARGV[13])
end
redis.call('HSET', KEYS[1], 'settled', '1')
return 1
