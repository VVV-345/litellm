"""集中定义 Redis 额度窗口使用的精确算术和原子 Lua 脚本片段。"""

from typing import Final

# Redis Lua 的 number 无法精确表示高位 Decimal，因此只把单个十进制数字转换为 number。
REDIS_UNSIGNED_DECIMAL_LUA: Final = r"""
local function quota_normalize_unsigned(value)
  if not string.match(value, '^%d+$') then return nil end
  local normalized = string.gsub(value, '^0+', '')
  if normalized == '' then return '0' end
  return normalized
end

local function quota_compare_unsigned(left, right)
  local normalized_left = quota_normalize_unsigned(left)
  local normalized_right = quota_normalize_unsigned(right)
  if not normalized_left or not normalized_right then return nil end
  if #normalized_left < #normalized_right then return -1 end
  if #normalized_left > #normalized_right then return 1 end
  if normalized_left < normalized_right then return -1 end
  if normalized_left > normalized_right then return 1 end
  return 0
end

local function quota_add_unsigned(left, right)
  local normalized_left = quota_normalize_unsigned(left)
  local normalized_right = quota_normalize_unsigned(right)
  if not normalized_left or not normalized_right then return nil end
  local left_index = #normalized_left
  local right_index = #normalized_right
  local carry = 0
  local result = ''
  while left_index > 0 or right_index > 0 or carry > 0 do
    local left_digit = left_index > 0 and tonumber(string.sub(normalized_left, left_index, left_index)) or 0
    local right_digit = right_index > 0 and tonumber(string.sub(normalized_right, right_index, right_index)) or 0
    local total = left_digit + right_digit + carry
    result = tostring(total % 10) .. result
    carry = math.floor(total / 10)
    left_index = left_index - 1
    right_index = right_index - 1
  end
  return quota_normalize_unsigned(result)
end

local function quota_subtract_unsigned(left, right)
  if quota_compare_unsigned(left, right) == -1 then return nil end
  local normalized_left = quota_normalize_unsigned(left)
  local normalized_right = quota_normalize_unsigned(right)
  if not normalized_left or not normalized_right then return nil end
  local left_index = #normalized_left
  local right_index = #normalized_right
  local borrow = 0
  local result = ''
  while left_index > 0 do
    local left_digit = tonumber(string.sub(normalized_left, left_index, left_index)) - borrow
    local right_digit = right_index > 0 and tonumber(string.sub(normalized_right, right_index, right_index)) or 0
    if left_digit < right_digit then
      left_digit = left_digit + 10
      borrow = 1
    else
      borrow = 0
    end
    result = tostring(left_digit - right_digit) .. result
    left_index = left_index - 1
    right_index = right_index - 1
  end
  return quota_normalize_unsigned(result)
end
"""

REDIS_CONFIGURE_QUOTA_WINDOW_SCRIPT: Final = (
    REDIS_UNSIGNED_DECIMAL_LUA
    + r"""
-- 厂商快照是校准点：保留校准点之后的本地 usage 和全部活动预占。
local current_observed_at = tonumber(redis.call('HGET', KEYS[1], 'observed_at') or '')
local incoming_observed_at = tonumber(ARGV[16])
if current_observed_at and incoming_observed_at < current_observed_at then return -1 end
if redis.call('HGET', KEYS[1], 'snapshot_fingerprint') == ARGV[1] then return 0 end

local retained_usage = redis.call('ZRANGEBYSCORE', KEYS[2], '(' .. ARGV[16], '+inf')
local retained_units = '0'
for _, member in ipairs(retained_usage) do
  local separator = string.find(member, '|', 1, true)
  if not separator then return -2 end
  retained_units = quota_add_unsigned(retained_units, string.sub(member, 1, separator - 1))
  if not retained_units then return -2 end
end

local calibrated_remaining = ARGV[12]
if calibrated_remaining ~= '' then
  if quota_compare_unsigned(calibrated_remaining, retained_units) == -1 then
    calibrated_remaining = '0'
  else
    calibrated_remaining = quota_subtract_unsigned(calibrated_remaining, retained_units)
  end
end
local reserved_units = redis.call('HGET', KEYS[1], 'reserved_units') or '0'

redis.call('HSET', KEYS[1],
  'snapshot_fingerprint', ARGV[1],
  'schema_version', ARGV[2],
  'window_id', ARGV[3],
  'account_id', ARGV[4],
  'scale', ARGV[5],
  'scope', ARGV[6],
  'subject_id', ARGV[7],
  'kind', ARGV[8],
  'window_type', ARGV[9],
  'duration_seconds', ARGV[10],
  'limit_units', ARGV[11],
  'snapshot_remaining_units', ARGV[12],
  'remaining_units', calibrated_remaining,
  'safety_reserve_units', ARGV[13],
  'reserved_units', reserved_units,
  'retry_at', ARGV[14],
  'reset_at', ARGV[15],
  'observed_at', ARGV[16],
  'source', ARGV[17],
  'reason_code', ARGV[18])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[16])
return 1
"""
)

REDIS_QUOTA_RUNTIME_LUA: Final = (
    REDIS_UNSIGNED_DECIMAL_LUA
    + r"""
-- usage 成员以“精确整数单位|租约 ID”保存，完整额度从不经过 tonumber。
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
  -- rolling 窗口每次操作前按事件逐条过期，并从厂商快照基线重新计算剩余量。
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
"""
)

REDIS_QUOTA_RESERVE_CHECK_LUA: Final = r"""
local quota_count = tonumber(ARGV[11])
local quota_states = {}
-- 先验证全部匹配窗口，避免某个窗口失败后留下部分预占。
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
"""

REDIS_QUOTA_RESERVE_COMMIT_LUA: Final = r"""
for quota_index, quota_state in ipairs(quota_states) do
  local updated_reserved = quota_add_unsigned(quota_state.reserved, quota_state.amount)
  redis.call('HSET', quota_state.window_key, 'reserved_units', updated_reserved)
  redis.call('ZADD', quota_state.window_key .. ':reservations', ARGV[7], ARGV[1])
  if quota_state.probe_key ~= '' then redis.call('SET', quota_state.probe_key, ARGV[1], 'EX', ARGV[10]) end
  redis.call('HSET', KEYS[3],
    'quota_window_' .. quota_index, quota_state.window_key,
    'quota_usage_' .. quota_index, quota_state.usage_key,
    'quota_amount_' .. quota_index, quota_state.amount,
    'quota_confirms_' .. quota_index, quota_state.confirms_reset,
    'quota_probe_' .. quota_index, quota_state.probe_key)
end
"""

REDIS_QUOTA_RELEASE_LUA: Final = r"""
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
"""

REDIS_QUOTA_HEARTBEAT_LUA: Final = r"""
local quota_count = tonumber(redis.call('HGET', KEYS[1], 'quota_count') or '0')
for quota_index = 1, quota_count do
  local window_key = redis.call('HGET', KEYS[1], 'quota_window_' .. quota_index)
  local quota_probe_key = redis.call('HGET', KEYS[1], 'quota_probe_' .. quota_index)
  if quota_probe_key and quota_probe_key ~= '' then
    if redis.call('GET', quota_probe_key) ~= lease_id then return 0 end
    redis.call('EXPIRE', quota_probe_key, ARGV[3])
  end
  if window_key then redis.call('ZADD', window_key .. ':reservations', ARGV[1], lease_id) end
end
"""

REDIS_QUOTA_SETTLE_LUA: Final = r"""
local quota_count = tonumber(redis.call('HGET', KEYS[1], 'quota_count') or '0')
local quota_states = {}
-- 第一轮只读取和验证；第二轮才统一更新窗口，降低部分结算风险。
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
"""
