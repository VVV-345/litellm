-- 本文件在创建租约前原子校验资格、并发容量和额度。
local requested_quota_count = tonumber(ARGV[11])
local expected_generation = ARGV[13 + requested_quota_count]
local runtime_generation = redis.call('GET', KEYS[10 + requested_quota_count * 2]) or ''
if expected_generation ~= '' and runtime_generation ~= expected_generation then
  return {0, '', 'quota_generation_mismatch'}
end
local existing = redis.call('GET', KEYS[4])
if existing then
  return {2, existing, 'existing'}
end
local probe_mode = ARGV[14 + requested_quota_count] == '1'
local absolute_expires_at = ARGV[15 + requested_quota_count]
local function exclusion_status(key, now, ignore_health)
  local entries = redis.call('HGETALL', key)
  local half_open = false
  local health_evidence = false
  for index = 1, #entries, 2 do
    local field = entries[index]
    local value = entries[index + 1]
    local field_separator = string.find(field, '|', 1, true)
    if not field_separator then return 'eligibility_state_invalid', false, false end
    local source = string.sub(field, 1, field_separator - 1)
    local reason = string.sub(field, field_separator + 1)
    local value_separator = string.find(value, '|', 1, true)
    if not value_separator then return 'eligibility_state_invalid', false, false end
    local retry_at = tonumber(string.sub(value, value_separator + 1)) or 0
    if retry_at == 0 or retry_at > now then
      if not ignore_health or source ~= 'health' then return reason, false, false end
      health_evidence = true
    else
      half_open = true
    end
  end
  return nil, half_open, health_evidence
end
local enabled = redis.call('HGET', KEYS[1], 'enabled')
local health = redis.call('HGET', KEYS[1], 'health')
local cooldown = tonumber(redis.call('HGET', KEYS[1], 'cooldown_until') or '0')
local inflight = tonumber(redis.call('GET', KEYS[2]) or '0')
local max_concurrency = tonumber(redis.call('HGET', KEYS[1], 'max_concurrency') or '0')
local quota_total = redis.call('HGET', KEYS[1], 'quota_total')
local quota_five = redis.call('HGET', KEYS[1], 'quota_five_hour')
local quota_weekly = redis.call('HGET', KEYS[1], 'quota_weekly')
local now = tonumber(ARGV[8])
if enabled ~= '1' or health == 'disabled' then return {0, '', 'disabled'} end
local probe_source_key = nil
local health_source_key = nil
for key_index = 6, 9 do
  local reason, half_open, health_evidence = exclusion_status(KEYS[key_index], now, probe_mode)
  if reason then return {0, '', reason} end
  if not probe_source_key and half_open then probe_source_key = KEYS[key_index] end
  if not health_source_key and health_evidence then health_source_key = KEYS[key_index] end
end
if not probe_mode and health == 'unhealthy' then return {0, '', 'unhealthy'} end
if not probe_mode and cooldown > now then return {0, '', 'cooldown'} end
if probe_mode and not probe_source_key then probe_source_key = health_source_key or KEYS[8] end
local probe_key = ''
if probe_source_key then
  probe_key = 'pool:eligibility:probe:' .. probe_source_key
  if redis.call('EXISTS', probe_key) == 1 then return {0, '', 'half_open_probe_inflight'} end
end
if inflight >= max_concurrency then return {0, '', 'capacity'} end
if quota_total and quota_total ~= '' and tonumber(quota_total) <= 0 then return {0, '', 'total_quota'} end
if quota_five and quota_five ~= '' and tonumber(quota_five) <= 0 then return {0, '', 'five_hour_quota'} end
if quota_weekly and quota_weekly ~= '' and tonumber(quota_weekly) <= 0 then return {0, '', 'weekly_quota'} end

local quota_count = tonumber(ARGV[11])
local quota_states = {}
for quota_index = 1, quota_count do
  local key_index = 8 + quota_index * 2
  local window_key = KEYS[key_index]
  local usage_key = KEYS[key_index + 1]
  local amount = ARGV[12 + quota_index]
  local remaining, quota_error = quota_refresh_rolling(window_key, usage_key, now, ARGV[12])
  if quota_error then return {0, '', quota_error} end
  local safety = redis.call('HGET', window_key, 'safety_reserve_units') or '0'
  local reserved = redis.call('HGET', window_key, 'reserved_units') or '0'
  local unavailable = quota_add_unsigned(safety, reserved)
  local with_reservation = quota_add_unsigned(unavailable or '', amount)
  if not unavailable or not with_reservation then return {0, '', 'quota_state_invalid'} end
  local confirms_reset = '0'
  local quota_probe_key = ''
  local available_base = remaining
  if remaining ~= '' and quota_compare_unsigned(remaining, unavailable) ~= 1 then
    local window_type = redis.call('HGET', window_key, 'window_type') or ''
    local retry_at = tonumber(redis.call('HGET', window_key, 'retry_at') or '')
    local limit = redis.call('HGET', window_key, 'limit_units') or ''
    if window_type ~= 'rolling' and retry_at and retry_at <= now and limit ~= '' then
      confirms_reset = '1'
      quota_probe_key = window_key .. ':probe'
      if redis.call('EXISTS', quota_probe_key) == 1 then return {0, '', 'half_open_probe_inflight'} end
      available_base = limit
    end
  end
  if available_base ~= '' then
    if quota_compare_unsigned(available_base, unavailable) ~= 1 then
      return {0, '', redis.call('HGET', window_key, 'reason_code') or 'quota_window_exhausted'}
    end
    if quota_compare_unsigned(available_base, with_reservation) == -1 then
      return {0, '', redis.call('HGET', window_key, 'reason_code') or 'quota_window_exhausted'}
    end
  end
  quota_states[quota_index] = {
    window_key = window_key,
    usage_key = usage_key,
    amount = amount,
    confirms_reset = confirms_reset,
    probe_key = quota_probe_key,
    reserved = reserved
  }
end

redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[3],
  'lease_id', ARGV[1], 'request_id', ARGV[2], 'account_id', ARGV[3],
  'deployment_id', ARGV[4], 'public_model', ARGV[5], 'billing_route_id', ARGV[6], 'expires_at', ARGV[7],
  'absolute_expires_at', absolute_expires_at,
  'probe_key', probe_key, 'quota_count', quota_count, 'generation_id', ARGV[13 + quota_count],
  'probe', ARGV[14 + quota_count], 'settled', '0', 'released', '0')
for quota_index, quota_state in ipairs(quota_states) do
  local updated_reserved = quota_add_unsigned(quota_state.reserved, quota_state.amount)
  redis.call('HSET', quota_state.window_key, 'reserved_units', updated_reserved)
  redis.call('ZADD', quota_state.window_key .. ':reservations', ARGV[7], ARGV[1])
  redis.call('ZADD', quota_state.window_key .. ':absolute_reservations', absolute_expires_at, ARGV[1])
  if quota_state.probe_key ~= '' then redis.call('SET', quota_state.probe_key, ARGV[1], 'EX', ARGV[10]) end
  redis.call('HSET', KEYS[3],
    'quota_window_' .. quota_index, quota_state.window_key,
    'quota_usage_' .. quota_index, quota_state.usage_key,
    'quota_amount_' .. quota_index, quota_state.amount,
    'quota_confirms_' .. quota_index, quota_state.confirms_reset,
    'quota_probe_' .. quota_index, quota_state.probe_key)
end
if probe_key ~= '' then redis.call('SET', probe_key, ARGV[1], 'EX', ARGV[10]) end
redis.call('SET', KEYS[4], ARGV[1], 'EX', ARGV[9])
redis.call('ZADD', KEYS[5], ARGV[7], ARGV[1])
return {1, ARGV[1], 'reserved'}
