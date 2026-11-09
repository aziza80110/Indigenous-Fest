"""Refresh representative CWA township forecasts for the website using county datasets."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
TAIPEI = ZoneInfo("Asia/Taipei")
SOURCE_URL = "https://opendata.cwa.gov.tw/dist/opendata-swagger.html"


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
            if not isinstance(item, dict):
                continue
            # CWA township responses use field-specific keys such as Weather,
            # MinTemperature and ProbabilityOfPrecipitation instead of Value.
            candidates = [item.get("Value"), item.get("value")]
            candidates.extend(item.get(name) for name in names)
            candidates.extend(item.values())
            for value in candidates:
                if value not in (None, "") and not isinstance(value, (dict, list)):
                    return str(value)
    return ""

def first_time(location: dict) -> tuple[str, str]:
    for element in location.get("WeatherElement", []):
        times = element.get("Time", [])
        if times:
            return times[0].get("StartTime", ""), times[0].get("EndTime", "")
    return "", ""


def get_town_rows(payload: dict) -> list[dict]:
    groups = payload.get("records", {}).get("Locations", [])
    if isinstance(groups, dict):
        groups = [groups]
    rows = []
    for group in groups:
        locations = group.get("Location", [])
        if isinstance(locations, list):
            rows.extend(locations)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--locations", type=Path, default=Path("automation/weather-locations.json"))
    parser.add_argument("--output", type=Path, default=Path("weather.json"))
    args = parser.parse_args()

    token = os.getenv("CWA_AUTHORIZATION", "").strip()
    if not token:
        raise SystemExit("未設定 CWA_AUTHORIZATION Secret")

    config = json.loads(args.locations.read_text(encoding="utf-8"))
    needed = {}
    for tribe in config:
        for place in tribe["locations"]:
            needed.setdefault(place["dataId"], set()).add(place["town"])

    towns_by_dataset = {}
    for index, (data_id, town_names) in enumerate(needed.items()):
        if index:
            time.sleep(1)
        response = requests.get(
            f"{BASE_URL}{data_id}",
            params={"Authorization": token, "format": "JSON"},
            timeout=30,
        )
        response.raise_for_status()
        rows = get_town_rows(response.json())
        if not rows:
            raise SystemExit(f"{data_id} API 有回應，但沒有鄉鎮資料")
        towns_by_dataset[data_id] = {row.get("LocationName"): row for row in rows}

    forecasts = []
    missing = []
    for tribe in config:
        for place in tribe["locations"]:
            location = towns_by_dataset.get(place["dataId"], {}).get(place["town"])
            if not location:
                missing.append(f"{tribe['tribe']}:{place['county']}{place['town']}")
                continue
            start, end = first_time(location)
            forecasts.append({
                "tribe": tribe["tribe"],
                "county": place["county"],
                "town": place["town"],
                "weather": first_value(location, {"天氣預報綜合描述", "WeatherDescription"}),
                "phenomenon": first_value(location, {"天氣現象", "Weather"}),
                "minTemperature": first_value(location, {"最低溫度", "MinTemperature"}),
                "maxTemperature": first_value(location, {"最高溫度", "MaxTemperature"}),
                "precipitationProbability": first_value(location, {"12小時降雨機率", "ProbabilityOfPrecipitation"}),
                "forecastStart": start,
                "forecastEnd": end,
                "datasetId": place["dataId"],
            })

    covered = len({row["tribe"] for row in forecasts})
    if covered < 17:
        raise SystemExit(f"匹配到 {covered}/17 族代表鄉鎮，未發布不完整資料。缺少：{', '.join(missing)}")

    output = {
        "source": "中央氣象署鄉鎮未來一週預報",
        "sourceUrl": SOURCE_URL,
        "generatedAt": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "matchedTownCount": len(forecasts),
        "forecasts": forecasts,
        "notice": "每族列出代表鄉鎮，不代表整個族群分布區域；祭典預報請以實際舉辦鄉鎮為準。資料通常涵蓋未來一週，較遠日期不可視為精確預報。",
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {len(forecasts)} 筆鄉鎮預報，涵蓋 {covered}/17 族。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
