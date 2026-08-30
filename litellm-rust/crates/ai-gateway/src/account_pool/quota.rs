//! 本文件把额度转换为精确定点十进制值，并匹配 Redis Lua 使用的额度窗口。

use sha2::{Digest, Sha256};
use thiserror::Error;

use super::config::{RuntimeAccount, RuntimeQuotaKind, RuntimeQuotaScope, RuntimeQuotaWindow};

pub(crate) const DECIMAL_SCALE: usize = 36;
const MAXIMUM_UNIT_DIGITS: usize = 256;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct QuotaReservation {
    pub window_key: String,
    pub usage_key: String,
    pub amount_units: String,
}

pub(crate) fn reservation_plan(
    account: &RuntimeAccount,
    public_model: &str,
    billing_route_id: Option<&str>,
    estimated_tokens: u64,
) -> Result<Vec<QuotaReservation>, QuotaEncodingError> {
    account
        .quota_windows
        .iter()
        .filter(|window| window_matches(window, public_model, billing_route_id))
        .map(|window| {
            let amount = match window.kind {
                RuntimeQuotaKind::Requests => "1".to_string(),
                RuntimeQuotaKind::Tokens => estimated_tokens.to_string(),
                RuntimeQuotaKind::Credits
                | RuntimeQuotaKind::Currency
                | RuntimeQuotaKind::ProviderUnits => "0".to_string(),
            };
            let window_key = quota_window_key(&account.id, &window.window_id);
            Ok(QuotaReservation {
                usage_key: format!("{window_key}:usage"),
                window_key,
                amount_units: encode_decimal(&amount)?,
            })
        })
        .collect()
}

pub(crate) fn settlement_units(
    input_tokens: u64,
    output_tokens: u64,
    cost_usd: Option<&str>,
) -> Result<(String, String, String), QuotaEncodingError> {
    let token_total = input_tokens
        .checked_add(output_tokens)
        .ok_or(QuotaEncodingError::ValueTooLarge)?;
    Ok((
        encode_decimal("1")?,
        encode_decimal(&token_total.to_string())?,
        cost_usd
            .map(encode_decimal)
            .transpose()?
            .unwrap_or_default(),
    ))
}

pub(crate) fn quota_window_key(account_id: &str, window_id: &str) -> String {
    let identity = format!("{:x}", Sha256::digest(window_id.as_bytes()));
    format!("pool:account:{account_id}:quota:{identity}")
}

fn window_matches(
    window: &RuntimeQuotaWindow,
    public_model: &str,
    billing_route_id: Option<&str>,
) -> bool {
    match window.scope {
        RuntimeQuotaScope::Channel => true,
        RuntimeQuotaScope::Model => window.subject_id.as_deref() == Some(public_model),
        RuntimeQuotaScope::BillingRoute => {
            billing_route_id.is_some() && window.subject_id.as_deref() == billing_route_id
        }
    }
}

fn encode_decimal(value: &str) -> Result<String, QuotaEncodingError> {
    let value = value.trim();
    if value.starts_with('-') || value.is_empty() {
        return Err(QuotaEncodingError::InvalidValue);
    }
    let value = value.strip_prefix('+').unwrap_or(value);
    let (mantissa, exponent) = split_exponent(value)?;
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    if whole.is_empty() && fraction.is_empty()
        || !whole.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(QuotaEncodingError::InvalidValue);
    }
    let digits = format!("{whole}{fraction}");
    let adjusted_exponent =
        exponent - i32::try_from(fraction.len()).map_err(|_| QuotaEncodingError::ValueTooLarge)?;
    let padding = adjusted_exponent + i32::try_from(DECIMAL_SCALE).expect("scale fits i32");
    let units = if padding >= 0 {
        let padding = usize::try_from(padding).map_err(|_| QuotaEncodingError::ValueTooLarge)?;
        format!("{digits}{}", "0".repeat(padding))
    } else {
        let discarded = usize::try_from(-padding).map_err(|_| QuotaEncodingError::ValueTooLarge)?;
        if discarded > digits.len()
            || digits[digits.len() - discarded..]
                .bytes()
                .any(|byte| byte != b'0')
        {
            return Err(QuotaEncodingError::ScaleTooLarge);
        }
        digits[..digits.len() - discarded].to_string()
    };
    let normalized = units.trim_start_matches('0');
    let normalized = if normalized.is_empty() {
        "0"
    } else {
        normalized
    };
    if normalized.len() > MAXIMUM_UNIT_DIGITS {
        return Err(QuotaEncodingError::ValueTooLarge);
    }
    Ok(normalized.to_string())
}

fn split_exponent(value: &str) -> Result<(&str, i32), QuotaEncodingError> {
    let lower = value.find('e');
    let upper = value.find('E');
    let separator = match (lower, upper) {
        (Some(_), Some(_)) => return Err(QuotaEncodingError::InvalidValue),
        (Some(index), None) | (None, Some(index)) => Some(index),
        (None, None) => None,
    };
    let Some(separator) = separator else {
        return Ok((value, 0));
    };
    let exponent = value[separator + 1..]
        .parse::<i32>()
        .map_err(|_| QuotaEncodingError::InvalidValue)?;
    Ok((&value[..separator], exponent))
}

#[derive(Debug, Error, PartialEq, Eq)]
pub(crate) enum QuotaEncodingError {
    #[error("quota value is not a non-negative finite decimal")]
    InvalidValue,
    #[error("quota value requires more than 36 decimal places")]
    ScaleTooLarge,
    #[error("quota value exceeds the Redis codec limit")]
    ValueTooLarge,
}

#[cfg(test)]
mod tests {
    use super::{DECIMAL_SCALE, QuotaEncodingError, encode_decimal};

    #[test]
    fn encodes_plain_and_exponent_decimals_at_python_scale() {
        assert_eq!(encode_decimal("1.25"), Ok(format!("125{}", "0".repeat(34))));
        assert_eq!(encode_decimal("1E+3"), Ok(format!("1{}", "0".repeat(39))));
        assert_eq!(encode_decimal("0"), Ok("0".to_string()));
        assert_eq!(DECIMAL_SCALE, 36);
    }

    #[test]
    fn rejects_negative_and_excess_precision() {
        assert_eq!(encode_decimal("-1"), Err(QuotaEncodingError::InvalidValue));
        assert_eq!(
            encode_decimal("0.0000000000000000000000000000000000001"),
            Err(QuotaEncodingError::ScaleTooLarge)
        );
    }
}
