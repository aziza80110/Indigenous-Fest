"""Refresh a small, public-safe subset of CWA township forecasts for the website."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093"
TAIPEI = ZoneInfo("Asia/Taipei")
SOURCE_URL = "https://opendata.cwa.gov.tw/dataset/forecast/F-D0047-093"


def first_value(location: dict, names: set[str]) -> str:
    for element in location.get("WeatherElement", []):
        if element.get("ElementName") not in names:
            continue
        times = element.get("Time", [])
        if not times:
            continue
        values = times[0].get("ElementValue", [])
        if isinstance(values, dict):
            values = [values]
        for item in values:
            value = item.get("Value", item.get("value"))
            if value not in (None, ""):
                return str(value)
    return ""


def first_time(location: dict) -> tuple[str, str]:
    for element in location.get("WeatherElement", []):
        times = element.get("Time", [])
        if times:
            return times[0].get("StartTime", ""), times[0].get("EndTime", "")
    return "", ""


def flatten_locations(groups: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def walk(value: object, county: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                walk(item, county)
        elif isinstance(value, dict):
            local_county = value.get("LocationsName") or value.get("CountyName") or county
            if value.get("LocationName") and value.get("WeatherElement"):
                key = (str(value.get("LocationName")), str(local_county))
                if key not in seen:
                    found.append({**value, "_county": str(local_county)})
                    seen.add(key)
            for key in ("Location", "Locations"):
                if key in value:
                    walk(value[key], str(local_county))

    walk(groups)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locations", type=Path, default=Path("automation/weather-locations.json"))
    parser.add_argument("--output", type=Path, default=Path("weather.json"))
    args = parser.parse_args()

    token = os.getenv("CWA_AUTHORIZATION", "").strip()
    if not token:
        raise SystemExit("未設定 CWA_AUTHORIZATION Secret")

    response = requests.get(
        BASE_URL,
        params={"Authorization": token, "format": "JSON"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    groups = payload.get("records", {}).get("Locations", [])
    locations = flatten_locations(groups)
    by_name: dict[str, list[dict]] = {}
    for location in locations:
        by_name.setdefault(location["LocationName"], []).append(location)

    config = json.loads(args.locations.read_text(encoding="utf-8"))
    forecasts = []
    missing = []
    for tribe in config:
        for town in tribe["towns"]:
            matches = by_name.get(town, [])
            if not matches:
                missing.append(f"{tribe['tribe']}:{town}")
                continue
            # Town names can repeat; preserve each county match for clarity.
            for location in matches:
                start, end = first_time(location)
                forecasts.append({
                    "tribe": tribe["tribe"],
                    "town": town,
                    "county": location.get("_county", ""),
                    "weather": first_value(location, {"天氣預報綜合描述", "WeatherDescription"}),
                    "phenomenon": first_value(location, {"天氣現象", "Weather"}),
                    "minTemperature": first_value(location, {"最低溫度", "MinTemperature"}),
                    "maxTemperature": first_value(location, {"最高溫度", "MaxTemperature"}),
                    "precipitationProbability": first_value(location, {"12小時降雨機率", "ProbabilityOfPrecipitation"}),
                    "forecastStart": start,
                    "forecastEnd": end,
                })

    if len({row["tribe"] for row in forecasts}) < 16:
        raise SystemExit(f"取得的鄉鎮資料不足16族；匹配 {len({row['tribe'] for row in forecasts})} 族，未寫入資料。")

    output = {
        "source": "中央氣象署鄉鎮未來一週預報",
        "sourceUrl": SOURCE_URL,
        "datasetId": "F-D0047-093",
        "generatedAt": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "matchedTownCount": len(forecasts),
        "forecasts": forecasts,
        "missingLocations": missing,
        "notice": "代表鄉鎮預報不等於整個族群分布區域的天氣；祭典地點請以實際舉辦鄉鎮為準。預報超過可用時段時，請不要將氣候平均視為即時預報。",
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {len(forecasts)} 筆鄉鎮預報，涵蓋 {len({row['tribe'] for row in forecasts})} 族；未匹配 {len(missing)} 個候選鄉鎮。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
