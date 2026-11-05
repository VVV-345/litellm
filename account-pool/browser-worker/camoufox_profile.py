"""在 Linux 镜像内生成及验证固定系统指纹，授权期间只读预装资源，不下载浏览器。"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import zlib
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

LOCALES: Final = ("en-US", "en-GB", "zh-CN")
BINARY: Final = Path("/opt/camoufox/camoufox-bin")


def generate(locale: str, os_name: str = "linux") -> dict[str, object]:
    if locale not in LOCALES and re.fullmatch(r"[A-Z]{2}", locale) is None:
        raise ValueError("unsupported locale")
    if os_name not in {"linux", "windows", "macos"}:
        raise ValueError("unsupported browser OS")
    from camoufox.fingerprints import Screen, generate_fingerprint
    from camoufox.locales import handle_locale

    resolved_locale: Final = handle_locale(locale).as_string
    fingerprint: Final = generate_fingerprint(
        os=os_name,
        screen=Screen(min_width=1920, max_width=1920, min_height=1080, max_height=1080),
    )
    # Xvfb 没有窗口管理器，真实窗口从原点显示；新指纹不能声称另一个位置，造成画面和点击坐标偏移。
    positioned: Final = {**fingerprint, "window": {**fingerprint["window"], "screenX": 0, "screenY": 0}}
    encoded: Final = base64.b64encode(zlib.compress(json.dumps(positioned, separators=(",", ":")).encode(), level=9)).decode()
    return {"version": "camoufox-0.5.9", "locale": resolved_locale, "os": os_name, "fingerprint": encoded}


def options(profile: dict[str, object]) -> dict[str, object]:
    required: Final = {"version", "locale", "fingerprint"}
    if not required <= profile.keys() <= required | {"timezone", "os"} or profile["version"] != "camoufox-0.5.9":
        raise ValueError("unsupported fingerprint version")
    os_name: Final = profile.get("os", "linux")
    if os_name not in {"linux", "windows", "macos"}:
        raise ValueError("unsupported browser OS")
    if not isinstance(profile["locale"], str) or not isinstance(profile["fingerprint"], str):
        raise ValueError("invalid fingerprint")
    timezone: Final = profile.get("timezone")
    if timezone is not None:
        if not isinstance(timezone, str):
            raise ValueError("invalid timezone")
        ZoneInfo(timezone)
    # SDK 原始指纹含大型 MIME 数据；无损压缩后传输，避免超过 Docker 环境变量和通道上限。
    packed: Final = base64.b64decode(profile["fingerprint"], validate=True)
    decoder: Final = zlib.decompressobj()
    raw: Final = decoder.decompress(packed, max_length=2_000_000)
    if not decoder.eof or decoder.unused_data:
        raise ValueError("invalid fingerprint size")
    fingerprint: Final = json.loads(raw)
    if not isinstance(fingerprint, dict):
        raise ValueError("invalid fingerprint object")
    if not BINARY.is_file():
        raise ValueError("preinstalled browser is missing")
    from camoufox.addons import DefaultAddons
    from camoufox.utils import launch_options

    result: Final = launch_options(
        executable_path=BINARY, fingerprint=fingerprint, locale=profile["locale"],
        os=os_name,
        config={"timezone": timezone} if timezone else {},
        i_know_what_im_doing=True,
        headless=False, block_webrtc=True, exclude_addons=list(DefaultAddons),
        proxy={"server": "http://egress-relay:8080", "bypass": "localhost,127.0.0.1,[::1]"},
        firefox_user_prefs={
            "network.proxy.failover_direct": False, "network.trr.mode": 5,
            "network.dns.disablePrefetch": True, "network.prefetch-next": False,
            "network.captive-portal-service.enabled": False, "network.connectivity-service.enabled": False,
        },
    )
    environment: Final = {
        key: value for key, value in result["env"].items()
        if key.startswith(("CAMOU_CONFIG_", "CAMOU_PREFS_")) or key in {"FONTCONFIG_FILE", "FONTCONFIG_PATH"}
    }
    return {
        "executablePath": str(BINARY), "args": result["args"], "headless": False,
        "proxy": result["proxy"], "firefoxUserPrefs": result["firefox_user_prefs"],
        "env": {
            **environment, "DISPLAY": ":99", "HOME": "/tmp", "PATH": os.environ.get("PATH", ""),
            **({"TZ": timezone} if timezone else {}),
        },
    }


def main() -> None:
    action: Final = sys.argv[1] if len(sys.argv) > 1 else "launch"
    if action == "generate":
        profile: Final = generate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "linux")
        options(profile)
        print(json.dumps(profile, separators=(",", ":")))
        return
    encoded: Final = os.environ.get("OAUTH_FINGERPRINT_PROFILE")
    decoded: Final = json.loads(encoded) if encoded else generate("en-US")
    if not isinstance(decoded, dict):
        raise ValueError("invalid fingerprint")
    print(json.dumps(options(cast(dict[str, object], decoded)), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Camoufox profile validation failed; check the pinned worker image") from None
