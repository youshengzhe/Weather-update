from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class NotificationError(RuntimeError):
    pass


def normalize_spt_message(message: str) -> str:
    return (
        message.replace("/", "／")
        .replace("\\", "＼")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def send_wxpusher_spt(message: str, spt: str) -> dict:
    normalized_message = normalize_spt_message(message)
    encoded_message = urllib.parse.quote(normalized_message, safe="")
    url = f"https://wxpusher.zjiecode.com/api/send/message/{spt}/{encoded_message}"
    request = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise NotificationError(f"微信推送失败，HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NotificationError(f"微信推送失败，网络错误: {exc}") from exc

    if not payload.get("success"):
        raise NotificationError(f"微信推送失败: {payload}")

    return payload
