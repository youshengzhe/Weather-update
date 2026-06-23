from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from notifier import NotificationError, send_wxpusher_spt
from weather_service import QWeatherClient, ResolvedLocation, WeatherApiError


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH)

    qweather = config.setdefault("qweather", {})
    credentials = qweather.setdefault("credentials", {})
    wxpusher = config.setdefault("wxpusher", {})

    qweather["api_host"] = os.getenv("QWEATHER_API_HOST", qweather.get("api_host", ""))
    credentials["kid"] = os.getenv("QWEATHER_KID", credentials.get("kid", ""))
    credentials["project_id"] = os.getenv("QWEATHER_PROJECT_ID", credentials.get("project_id", ""))
    credentials["private_key_pem"] = os.getenv(
        "QWEATHER_PRIVATE_KEY_PEM",
        credentials.get("private_key_pem", ""),
    )
    wxpusher["spt"] = os.getenv("WXPUSHER_SPT", wxpusher.get("spt", ""))

    return config


def get_client(config: dict[str, Any]) -> QWeatherClient:
    qweather = config["qweather"]
    credentials = qweather["credentials"]
    return QWeatherClient(
        api_host=qweather["api_host"],
        kid=credentials["kid"],
        project_id=credentials["project_id"],
        private_key_pem=credentials["private_key_pem"],
        lang=qweather.get("lang", "zh"),
        unit=qweather.get("unit", "m"),
    )


def get_configured_locations(config: dict[str, Any]) -> list[dict[str, Any]]:
    if "locations" in config:
        return config["locations"]
    if "location" in config:
        return [config["location"]]
    raise WeatherApiError("未配置地点")


def get_location_summary(location: ResolvedLocation) -> str:
    parts = [location.adm1, location.adm2, location.name]
    text = "".join(part for part in parts if part)
    return text or location.name


def parse_qweather_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def is_precipitation(text: str, pop: int, precip: float) -> bool:
    return any(keyword in text for keyword in ("雨", "雪", "雷")) or pop >= 35 or precip > 0


def precipitation_kind(text: str) -> str:
    if "雪" in text:
        return "雪"
    if "雨" in text or "雷" in text:
        return "雨"
    return "雨雪"


def needs_more_clothes(temp_min: int, temp_max: int | None = None) -> bool:
    if temp_min <= 18:
        return True
    if temp_max is not None and (temp_max - temp_min) >= 10 and temp_min <= 20:
        return True
    return False


def umbrella_text(required: bool, start_hint: str = "") -> str:
    if required:
        return f"【带伞】{start_hint or '请提前带伞'}"
    return "带伞：不用"


def clothes_text(required: bool) -> str:
    return "【添衣】注意加衣" if required else "添衣：不用"


def in_quiet_hours(config: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    schedule = config["schedule"]
    quiet_start = int(schedule.get("quiet_start_hour", 22))
    quiet_end = int(schedule.get("quiet_end_hour", 8))
    return now.hour >= quiet_start or now.hour < quiet_end


def get_hourly_window(hourly_payload: dict[str, Any], hours: int, start: datetime | None = None) -> list[dict[str, Any]]:
    start = start or datetime.now().replace(minute=0, second=0, microsecond=0)
    end_time = start + timedelta(hours=hours)
    upcoming = []
    for item in hourly_payload.get("hourly", []):
        fx_time = item.get("fxTime")
        if not fx_time:
            continue
        item_time = parse_qweather_time(fx_time)
        if start <= item_time < end_time:
            upcoming.append(item)
    return upcoming


def first_precipitation_event(items: list[dict[str, Any]]) -> tuple[datetime, str] | None:
    for item in items:
        text = item.get("text", "")
        pop = int(item.get("pop", "0") or 0)
        precip = float(item.get("precip", "0") or 0)
        if is_precipitation(text, pop, precip):
            return parse_qweather_time(item["fxTime"]), precipitation_kind(text)
    return None


def summarize_weather_texts(items: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for item in items:
        text = item.get("text", "")
        if text and text not in texts:
            texts.append(text)
    return "转".join(texts[:3]) if texts else "未知"


def build_next_day_message(location: ResolvedLocation, daily_payload: dict[str, Any], hourly_payload: dict[str, Any]) -> str:
    daily = daily_payload.get("daily", [])
    if len(daily) < 2:
        raise WeatherApiError("每日预报返回不足 2 天，无法生成提醒")

    tomorrow = daily[1]
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    next_day_items = get_hourly_window(hourly_payload, 24, start=now)
    event = first_precipitation_event(next_day_items)

    weather_text = summarize_weather_texts(next_day_items) or tomorrow.get("textDay") or tomorrow.get("textNight") or "未知"
    temp_min = int(float(tomorrow.get("tempMin", "0") or 0))
    temp_max = int(float(tomorrow.get("tempMax", "0") or 0))
    clothes_required = needs_more_clothes(temp_min, temp_max)

    if event:
        start_time, kind = event
        umbrella_required = True
        umbrella_hint = f"预计{start_time:%H:%M}开始下{kind}"
    else:
        umbrella_required = False
        umbrella_hint = ""

    return "\n".join(
        [
            f"时间：接下来一天（至{(now + timedelta(hours=24)):%m-%d %H:%M}）",
            f"地址：{get_location_summary(location)}",
            f"天气：{weather_text}，{temp_min}~{temp_max}℃",
            umbrella_text(umbrella_required, umbrella_hint),
            clothes_text(clothes_required),
        ]
    )


def should_push_hourly(config: dict[str, Any], upcoming: list[dict[str, Any]]) -> tuple[bool, str]:
    if in_quiet_hours(config):
        return False, "夜间静默时段，不推送。"
    if not upcoming:
        return False, "未来三小时无数据。"

    precip_flags = [
        is_precipitation(
            item.get("text", ""),
            int(item.get("pop", "0") or 0),
            float(item.get("precip", "0") or 0),
        )
        for item in upcoming
    ]

    if not any(precip_flags):
        return False, "未来三小时无雨雪，不推送。"
    if precip_flags[0] and any(precip_flags[1:]):
        return False, "当前已在下雨或下雪且后续仍会持续，不重复推送。"
    return True, ""


def build_hourly_message(location: ResolvedLocation, upcoming: list[dict[str, Any]]) -> str:
    if not upcoming:
        raise WeatherApiError("逐小时预报里没有未来三小时的数据")

    temps = [int(float(item.get("temp", "0") or 0)) for item in upcoming]
    weather_parts = []
    first_event = first_precipitation_event(upcoming)
    for item in upcoming:
        item_time = parse_qweather_time(item["fxTime"])
        weather_parts.append(f"{item_time:%H:%M} {item.get('text', '')}")

    clothes_required = needs_more_clothes(min(temps), max(temps))
    start_time = parse_qweather_time(upcoming[0]["fxTime"])
    end_time = parse_qweather_time(upcoming[-1]["fxTime"])

    if first_event:
        event_time, event_kind = first_event
        umbrella_required = True
        umbrella_hint = f"预计{event_time:%H:%M}开始下{event_kind}"
    else:
        umbrella_required = False
        umbrella_hint = ""

    return "\n".join(
        [
            f"时间：{start_time:%Y-%m-%d %H:%M} - {end_time:%H:%M}",
            f"地址：{get_location_summary(location)}",
            f"天气：{'，'.join(weather_parts)}，{min(temps)}~{max(temps)}℃",
            umbrella_text(umbrella_required, umbrella_hint),
            clothes_text(clothes_required),
        ]
    )


def send_message(config: dict[str, Any], message: str) -> None:
    spt = config["wxpusher"]["spt"]
    if not spt:
        raise NotificationError("未配置 WXPUSHER_SPT")
    response = send_wxpusher_spt(message, spt)
    print(json.dumps(response, ensure_ascii=False, indent=2))


def send_for_each_location(config: dict[str, Any], builder: Callable[[QWeatherClient, ResolvedLocation], str]) -> None:
    client = get_client(config)
    for location_config in get_configured_locations(config):
        location = client.resolve_location(location_config)
        send_message(config, builder(client, location))


def command_test_wechat(config: dict[str, Any]) -> None:
    for location_config in get_configured_locations(config):
        send_message(
            config,
            "\n".join(
                [
                    "时间：测试",
                    f"地址：{location_config.get('query', '未命名地点')}",
                    "天气：通道正常",
                    "带伞：不用",
                    "添衣：不用",
                ]
            ),
        )


def command_tomorrow(config: dict[str, Any]) -> None:
    def builder(client: QWeatherClient, location: ResolvedLocation) -> str:
        daily_payload = client.get_daily_forecast(location, "3d")
        hourly_payload = client.get_hourly_forecast(location, "24h")
        return build_next_day_message(location, daily_payload, hourly_payload)

    send_for_each_location(config, builder)


def command_hourly(config: dict[str, Any]) -> None:
    client = get_client(config)
    lookahead_hours = int(config["schedule"].get("rain_lookahead_hours", 3))
    for location_config in get_configured_locations(config):
        location = client.resolve_location(location_config)
        payload = client.get_hourly_forecast(location, "24h")
        upcoming = get_hourly_window(payload, lookahead_hours)
        should_push, reason = should_push_hourly(config, upcoming)
        if not should_push:
            print(f"{location_config.get('query', get_location_summary(location))}：{reason}")
            continue
        send_message(config, build_hourly_message(location, upcoming))


def command_set_location(config: dict[str, Any], index: int, query: str) -> None:
    locations = get_configured_locations(config)
    if index < 0 or index >= len(locations):
        raise WeatherApiError(f"地点序号超出范围：{index}")
    locations[index]["query"] = query
    locations[index]["location_id"] = ""
    locations[index]["longitude"] = ""
    locations[index]["latitude"] = ""
    config["locations"] = locations
    config.pop("location", None)
    save_json(CONFIG_PATH, config)
    print(f"已更新第 {index} 个地点为：{query}")


def command_resolve_location(config: dict[str, Any]) -> None:
    client = get_client(config)
    locations = get_configured_locations(config)
    resolved = []
    for location_config in locations:
        location_config["location_id"] = ""
        location_config["longitude"] = ""
        location_config["latitude"] = ""
        location = client.resolve_location(location_config)
        location_config["location_id"] = location.location_id
        location_config["longitude"] = location.longitude
        location_config["latitude"] = location.latitude
        if location.name:
            location_config["query"] = get_location_summary(location)
        resolved.append(
            {
                "name": location.name,
                "location_id": location.location_id,
                "longitude": location.longitude,
                "latitude": location.latitude,
                "adm1": location.adm1,
                "adm2": location.adm2,
            }
        )
    config["locations"] = locations
    config.pop("location", None)
    save_json(CONFIG_PATH, config)
    print(json.dumps(resolved, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="天气通知")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("test-wechat", help="测试微信推送")
    subparsers.add_parser("tomorrow", help="发送接下来一天的天气提醒")
    subparsers.add_parser("hourly", help="按规则检查未来三小时是否需要推送")
    subparsers.add_parser("resolve-location", help="重新解析所有地点，优先匹配区县")

    set_location = subparsers.add_parser("set-location", help="修改某个地点")
    set_location.add_argument("index", type=int, help="地点序号，从 0 开始")
    set_location.add_argument("query", help="地点名称，例如 成都市武侯区")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config()

    try:
        if args.command == "test-wechat":
            command_test_wechat(config)
        elif args.command == "tomorrow":
            command_tomorrow(config)
        elif args.command == "hourly":
            command_hourly(config)
        elif args.command == "set-location":
            command_set_location(config, args.index, args.query)
        elif args.command == "resolve-location":
            command_resolve_location(config)
        else:
            parser.print_help()
            return 1
    except (WeatherApiError, NotificationError) as exc:
        print(f"执行失败：{exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
