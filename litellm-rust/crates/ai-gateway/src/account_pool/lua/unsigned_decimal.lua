-- Exact unsigned integer arithmetic for fixed-scale quota values.
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
