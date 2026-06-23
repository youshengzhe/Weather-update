from __future__ import annotations

import base64
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class WeatherApiError(RuntimeError):
    pass


@dataclass
class ResolvedLocation:
    name: str
    location_id: str
    longitude: str
    latitude: str
    adm1: str = ""
    adm2: str = ""


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class QWeatherClient:
    def __init__(
        self,
        api_host: str,
        kid: str,
        project_id: str,
        private_key_pem: str,
        lang: str = "zh",
        unit: str = "m",
    ) -> None:
        self.api_host = api_host.rstrip("/")
        self.kid = kid
        self.project_id = project_id
        self.private_key_pem = private_key_pem
        self.lang = lang
        self.unit = unit

    def _generate_jwt(self) -> str:
        now = int(time.time())
        header = {"alg": "EdDSA", "kid": self.kid}
        payload = {"sub": self.project_id, "iat": now - 30, "exp": now + 3600}

        header_b64 = _base64url_encode(
            json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        payload_b64 = _base64url_encode(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

        private_key = serialization.load_pem_private_key(
            self.private_key_pem.encode("utf-8"),
            password=None,
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            raise WeatherApiError("和风天气私钥不是 Ed25519 格式，无法生成 JWT")

        signature = private_key.sign(signing_input)
        signature_b64 = _base64url_encode(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def _request(self, path: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{self.api_host}{path}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._generate_jwt()}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "weather-notifier/1.0",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw_body = response.read()
                if "gzip" in response.headers.get("Content-Encoding", "").lower():
                    raw_body = gzip.decompress(raw_body)
                payload = json.loads(raw_body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw_detail = exc.read()
            if "gzip" in (exc.headers.get("Content-Encoding", "").lower() if exc.headers else ""):
                try:
                    raw_detail = gzip.decompress(raw_detail)
                except OSError:
                    pass
            detail = raw_detail.decode("utf-8", errors="ignore")
            raise WeatherApiError(f"和风天气请求失败，HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise WeatherApiError(f"和风天气请求失败，网络错误: {exc}") from exc

        if payload.get("code") != "200":
            raise WeatherApiError(f"和风天气返回异常: {payload}")

        return payload

    def _location_score(self, query: str, item: dict[str, str]) -> tuple[int, int]:
        score = 0
        name = item.get("name", "")
        adm1 = item.get("adm1", "")
        adm2 = item.get("adm2", "")
        loc_type = item.get("type", "")

        if query and name and name in query:
            score += 10
        if query and adm2 and adm2 in query:
            score += 6
        if query and adm1 and adm1 in query:
            score += 3

        district_keywords = ("区", "县", "旗")
        if any(keyword in query for keyword in district_keywords):
            if any(keyword in name for keyword in district_keywords):
                score += 20
            if loc_type in {"district", "county"}:
                score += 20
        elif loc_type == "city":
            score += 5

        if query and name == query:
            score += 30
        if query and query.endswith(name):
            score += 15

        try:
            rank = int(item.get("rank", "999"))
        except ValueError:
            rank = 999
        return score, -rank

    def resolve_location(self, config_location: dict) -> ResolvedLocation:
        if config_location.get("location_id"):
            return ResolvedLocation(
                name=config_location.get("query", config_location["location_id"]),
                location_id=config_location["location_id"],
                longitude=str(config_location.get("longitude", "")),
                latitude=str(config_location.get("latitude", "")),
            )

        if config_location.get("longitude") and config_location.get("latitude"):
            return ResolvedLocation(
                name=config_location.get("query", "自定义坐标"),
                location_id="",
                longitude=str(config_location["longitude"]),
                latitude=str(config_location["latitude"]),
            )

        query = config_location.get("query", "").strip()
        if not query:
            raise WeatherApiError("未配置位置，请在配置里填写 query")

        payload = self._request("/geo/v2/city/lookup", {"location": query, "lang": self.lang})
        locations = payload.get("location") or []
        if not locations:
            raise WeatherApiError(f"未找到地点: {query}")

        best = max(locations, key=lambda item: self._location_score(query, item))
        return ResolvedLocation(
            name=best.get("name", query),
            location_id=best.get("id", ""),
            longitude=best.get("lon", ""),
            latitude=best.get("lat", ""),
            adm1=best.get("adm1", ""),
            adm2=best.get("adm2", ""),
        )

    def get_daily_forecast(self, location: ResolvedLocation, days: str = "3d") -> dict:
        location_ref = location.location_id or f"{location.longitude},{location.latitude}"
        return self._request(
            f"/v7/weather/{days}",
            {"location": location_ref, "lang": self.lang, "unit": self.unit},
        )

    def get_hourly_forecast(self, location: ResolvedLocation, hours: str = "24h") -> dict:
        location_ref = location.location_id or f"{location.longitude},{location.latitude}"
        return self._request(
            f"/v7/weather/{hours}",
            {"location": location_ref, "lang": self.lang, "unit": self.unit},
        )
