-- Runtime normalization for rolling and fixed quota windows.
local function quota_sum_usage(usage_key)
  local members = redis.call('ZRANGE', usage_key, 0, -1)
  local total = '0'
  for _, member in ipairs(members) do
    local separator = string.find(member, '|', 1, true)
    if not separator then return nil end
    total = quota_add_unsigned(total, string.sub(member, 1, separator - 1))
    if not total then return nil end
  end
  return total
end

local function quota_next_boundary(window_key, usage_key, now)
  local duration = tonumber(redis.call('HGET', window_key, 'duration_seconds') or '')
  local observed_at = tonumber(redis.call('HGET', window_key, 'observed_at') or '')
  if not duration or not observed_at then return nil end
  local snapshot_boundary = observed_at + duration
  local first_usage = redis.call('ZRANGE', usage_key, 0, 0, 'WITHSCORES')
  local usage_boundary = nil
  if #first_usage == 2 then usage_boundary = tonumber(first_usage[2]) + duration end
  if snapshot_boundary <= now then snapshot_boundary = nil end
  if usage_boundary and usage_boundary <= now then usage_boundary = nil end
  if snapshot_boundary and usage_boundary then return math.min(snapshot_boundary, usage_boundary) end
  return snapshot_boundary or usage_boundary
end

local function quota_refresh_rolling(window_key, usage_key, now, expected_scale)
  if redis.call('EXISTS', window_key) == 0 then return nil, 'quota_state_invalid' end
  if redis.call('HGET', window_key, 'scale') ~= expected_scale then return nil, 'quota_state_invalid' end
  local remaining = redis.call('HGET', window_key, 'remaining_units')
  if remaining == false then return nil, 'quota_state_invalid' end
  if redis.call('HGET', window_key, 'window_type') ~= 'rolling' then return remaining, nil end
  local duration = tonumber(redis.call('HGET', window_key, 'duration_seconds') or '')
  local observed_at = tonumber(redis.call('HGET', window_key, 'observed_at') or '')
  if not duration or not observed_at then return nil, 'quota_state_invalid' end
  local cutoff = now - duration
  redis.call('ZREMRANGEBYSCORE', usage_key, '-inf', cutoff)
  local baseline = redis.call('HGET', window_key, 'snapshot_remaining_units') or ''
  if observed_at + duration <= now then baseline = redis.call('HGET', window_key, 'limit_units') or '' end
  if baseline == '' then
    redis.call('HSET', window_key, 'remaining_units', '', 'retry_at', '')
    return '', nil
  end
  local consumed = quota_sum_usage(usage_key)
  if not consumed then return nil, 'quota_state_invalid' end
  if quota_compare_unsigned(baseline, consumed) == -1 then
    remaining = '0'
  else
    remaining = quota_subtract_unsigned(baseline, consumed)
  end
  local safety = redis.call('HGET', window_key, 'safety_reserve_units') or '0'
  local reserved = redis.call('HGET', window_key, 'reserved_units') or '0'
  local unavailable = quota_add_unsigned(safety, reserved)
  if not unavailable then return nil, 'quota_state_invalid' end
  local retry_at = ''
  if quota_compare_unsigned(remaining, unavailable) ~= 1 then
    local boundary = quota_next_boundary(window_key, usage_key, now)
    if boundary then retry_at = tostring(boundary) end
  end
  redis.call('HSET', window_key, 'remaining_units', remaining, 'retry_at', retry_at)
  return remaining, nil
end

local function quota_next_fixed_retry(window_key, now)
  local retry_at = tonumber(redis.call('HGET', window_key, 'retry_at') or '')
  local duration = tonumber(redis.call('HGET', window_key, 'duration_seconds') or '')
  if not retry_at or not duration then return '' end
  local elapsed_periods = math.floor((now - retry_at) / duration) + 1
  return tostring(retry_at + elapsed_periods * duration)
end
