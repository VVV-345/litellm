"""在镜像内生成及验证固定 Linux 指纹，授权期间只读预装资源，不下载浏览器。"""

from __future__ import annotations

import json
import base64
import os
import sys
import zlib
from pathlib import Path
from typing import Final, cast

LOCALES: Final = ("en-US", "en-GB", "zh-CN")
BINARY: Final = Path("/opt/camoufox/camoufox-bin")


def generate(locale: str) -> dict[str, object]:
    if locale not in LOCALES:
        raise ValueError("unsupported locale")
    from camoufox.fingerprints import Screen, generate_fingerprint

    fingerprint: Final = generate_fingerprint(
        os="linux", window=(1280, 800),
        screen=Screen(min_width=1280, max_width=1920, min_height=800, max_height=1080),
    )
    encoded: Final = base64.b64encode(zlib.compress(json.dumps(fingerprint, separators=(",", ":")).encode(), level=9)).decode()
    return {"version": "camoufox-0.5.9", "locale": locale, "fingerprint": encoded}


def options(profile: dict[str, object]) -> dict[str, object]:
    if profile.keys() != {"version", "locale", "fingerprint"} or profile["version"] != "camoufox-0.5.9":
        raise ValueError("unsupported fingerprint version")
    if profile["locale"] not in LOCALES or not isinstance(profile["fingerprint"], str):
        raise ValueError("invalid fingerprint")
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
        "env": {**environment, "DISPLAY": ":99", "HOME": "/tmp", "PATH": os.environ.get("PATH", "")},
    }


def main() -> None:
    action: Final = sys.argv[1] if len(sys.argv) > 1 else "launch"
    if action == "generate":
        profile: Final = generate(sys.argv[2])
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
