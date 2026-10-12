from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlparse

from app.config import settings


PLATFORM_HOSTS = {
    "douyin.com",
    "iesdouyin.com",
    "kuaishou.com",
    "kuaishou.cn",
    "m.chenzhongtech.com",
    "m.gifshow.com",
    "xhslink.com",
    "xhslink.cn",  # 小红书 App 分享出来的短链接用这个域名
    "xiaohongshu.com",
    "weixin.qq.com",
}


def host_matches(host: str, allowed: set[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(normalized == item or normalized.endswith(f".{item}") for item in allowed)


def validate_platform_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("链接必须使用 HTTP 或 HTTPS")
    if parsed.username or parsed.password or not host_matches(parsed.hostname, PLATFORM_HOSTS):
        raise ValueError("仅支持抖音、快手、小红书和视频号链接")
    return url


def extract_platform_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>\"']+", text)
    if not match:
        raise ValueError("没有找到有效链接")
    return validate_platform_url(match.group(0).rstrip("，。；、!?！？)]}"))


def ensure_public_destination(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("下载地址无效")
    if parsed.username or parsed.password:
        raise ValueError("下载地址包含非法凭据")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("无法解析下载服务器地址") from exc
    if not addresses:
        raise ValueError("下载服务器没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("下载地址指向非公网服务器")


def create_download_token(url: str, headers: dict[str, str], filename: str) -> str:
    payload = {
        "url": url,
        "headers": headers,
        "filename": filename,
        "exp": int(time.time()) + settings.token_ttl,
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(settings.app_secret.encode(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def decode_download_token(token: str) -> dict:
    try:
        encoded_text, signature_text = token.split(".", 1)
        encoded = encoded_text.encode()
        signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        canonical_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        if not hmac.compare_digest(signature_text, canonical_signature):
            raise ValueError("下载凭证无效")
        expected = hmac.new(settings.app_secret.encode(), encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("下载凭证无效")
        raw = base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4))
        payload = json.loads(raw)
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("下载凭证已过期，请重新解析")
        if not isinstance(payload.get("url"), str) or not isinstance(payload.get("filename"), str):
            raise ValueError("下载凭证格式错误")
        if not isinstance(payload.get("headers"), dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in payload["headers"].items()
        ):
            raise ValueError("下载凭证格式错误")
        return payload
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("下载凭证格式错误") from exc


def safe_filename(value: str, fallback: str = "video") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return (value[:100] or fallback) + ".mp4"


def normalize_https_url(url: str | None) -> str | None:
    if url and url.startswith("http://"):
        return f"https://{url[7:]}"
    return url


def validate_range_header(value: str | None) -> str | None:
    if not value:
        return None
    if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", value.strip()):
        raise ValueError("Range 请求格式无效")
    return value.strip()
