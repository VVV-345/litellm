//! 本文件延迟拼装 Redis Lua 脚本，供租约预占、续租、结算和释放共用。

use std::sync::LazyLock;

const UNSIGNED_DECIMAL: &str = include_str!("lua/unsigned_decimal.lua");
const QUOTA_RUNTIME: &str = include_str!("lua/quota_runtime.lua");

pub(crate) static RESERVE: LazyLock<String> = LazyLock::new(|| {
    [
        UNSIGNED_DECIMAL,
        QUOTA_RUNTIME,
        include_str!("lua/reserve.lua"),
    ]
    .concat()
});

pub(crate) static RELEASE: LazyLock<String> = LazyLock::new(|| {
    [
        UNSIGNED_DECIMAL,
        QUOTA_RUNTIME,
        include_str!("lua/release.lua"),
    ]
    .concat()
});

pub(crate) static HEARTBEAT: &str = include_str!("lua/heartbeat.lua");

pub(crate) static SETTLE: LazyLock<String> = LazyLock::new(|| {
    [
        UNSIGNED_DECIMAL,
        QUOTA_RUNTIME,
        include_str!("lua/settle.lua"),
    ]
    .concat()
});
