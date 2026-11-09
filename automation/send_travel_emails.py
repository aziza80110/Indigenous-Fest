"""GitHub Actions worker for dated Indigenous festival travel brief emails.

The workflow commits claims before sending and commits final delivery status
afterward. Resend idempotency keys provide a second deduplication layer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
CWA_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
CWA_DATASET_BY_COUNTY = {
    "宜蘭縣": "F-D0047-003",
    "桃園市": "F-D0047-007",
    "新竹縣": "F-D0047-011",
    "苗栗縣": "F-D0047-015",
    "南投縣": "F-D0047-023",
    "嘉義縣": "F-D0047-031",
    "屏東縣": "F-D0047-035",
    "臺東縣": "F-D0047-039",
    "花蓮縣": "F-D0047-043",
    "高雄市": "F-D0047-067",
    "臺南市": "F-D0047-079",
    "新北市": "F-D0047-071",
    "臺中市": "F-D0047-075",
}
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
DEFAULT_EVENTS = Path("automation/festivals.json")
DEFAULT_STATE = Path("automation/mail-delivery-state.json")


def parse_today(value: str | None = None) -> date:
    return date.fromisoformat(value) if value else datetime.now(TAIPEI).date()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temp_path = Path(stream.name)
    temp_path.replace(path)


def event_key(event: dict[str, Any], stage: str, recipient: str) -> str:
    raw = f"{event['id']}|{event['startDate']}|{stage}|{recipient}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def active_subscriptions() -> list[dict[str, Any]]:
    """Read only active, verified opt-ins from Firestore using the GitHub secret."""
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return []
    try:
        from firebase_admin import credentials, firestore, get_app, initialize_app
        try:
            app = get_app("festival-mail-subscriber-reader")
        except ValueError:
            app = initialize_app(
                credentials.Certificate(json.loads(raw)),
                name="festival-mail-subscriber-reader",
            )
        database = firestore.client(app=app)
        rows = []
        for snapshot in database.collection("mailSubscriptions").where("active", "==", True).stream():
            data = snapshot.to_dict()
            if data.get("consent") is True and data.get("email") and data.get("tribes"):
                rows.append({"token": snapshot.id, **data})
        return rows
    except Exception as exc:
        raise RuntimeError("無法讀取 Firebase 訂閱名單；請檢查 FIREBASE_SERVICE_ACCOUNT_JSON 權限") from exc


def recipients_for_event(event: dict[str, Any], owner_email: str) -> list[dict[str, str]]:
    """Combine the owner's existing alerts with opted-in visitors matching this event."""
    recipients: dict[str, dict[str, str]] = {}
    if owner_email.strip():
        email = owner_email.strip().lower()
        recipients[email] = {"email": email, "unsubscribe_token": ""}
    for subscription in active_subscriptions():
        preferences = subscription.get("tribes", [])
        if "all" not in preferences and event.get("tribe") not in preferences:
            continue
        email = str(subscription.get("email", "")).strip().lower()
        token = str(subscription.get("token", ""))
        if email and token:
            recipients[email] = {"email": email, "unsubscribe_token": token}
    return list(recipients.values())


def event_stage(event: dict[str, Any], today: date) -> str | None:
    # Reference windows and estimated dates are display-only. Email scheduling
    # requires an exact date confirmed by an official event announcement.
    if (
        event.get("active") is not True
        or event.get("dateStatus") != "confirmed"
        or event.get("datePrecision") != "exact"
        or not event.get("sourceUrl")
    ):
        return None
    try:
        start = date.fromisoformat(event["startDate"])
    except (KeyError, TypeError, ValueError):
        return None
    days = (start - today).days
    if 15 <= days <= 30:
        return "planning"
    if 1 <= days <= 7:
        return "weather_update"
    return None


def due_events(events: list[dict[str, Any]], today: date) -> list[tuple[dict[str, Any], str]]:
    due = []
    seen = set()
    for event in events:
        event_id = event.get("id")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        stage = event_stage(event, today)
        if stage:
            due.append((event, stage))
    return due


def can_claim(record: dict[str, Any] | None, now: datetime) -> bool:
    if not record or record.get("status") in {"failed", "pending"}:
        return True
    if record.get("status") == "sent":
        return False
    if record.get("status") == "claimed":
        try:
            claimed_at = datetime.fromisoformat(record["claimedAt"])
        except (KeyError, TypeError, ValueError):
            return True
        return now - claimed_at >= timedelta(hours=1)
    return True


def claim_due(events: list[dict[str, Any]], state: dict[str, Any], today: date,
              recipient: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    claimed = []
    for event, stage in due_events(events, today):
        for target in recipients_for_event(event, recipient):
            email = target["email"]
            key = event_key(event, stage, email)
            if not can_claim(state.get(key), now):
                continue
            state[key] = {
                "eventId": event["id"],
                "eventName": event.get("name", event["id"]),
                "startDate": event["startDate"],
                "stage": stage,
                "recipientHash": hashlib.sha256(email.encode("utf-8")).hexdigest(),
                "status": "claimed",
                "claimedAt": now.isoformat(),
            }
            claimed.append(key)
    return claimed


def cwa_forecast(town: str, county: str = "") -> dict[str, Any]:
    import requests

    token = os.getenv("CWA_AUTHORIZATION", "").strip()
    if not token:
        return {"status": "not_configured", "message": "尚未設定中央氣象署授權碼"}
    dataset = CWA_DATASET_BY_COUNTY.get(county)
    if not dataset:
        return {"status": "unavailable", "message": f"活動資料未提供可查詢的縣市：{county or '未指定'}"}
    response = requests.get(
        f"{CWA_BASE}/{dataset}",
        params={"Authorization": token, "format": "JSON", "LocationName": town},
        timeout=15,
    )
    response.raise_for_status()
    records = response.json().get("records", {}).get("Locations", [{}])[0].get("Location", [])
    match = next((row for row in records if row.get("LocationName") == town), None)
    if not match:
        return {"status": "unavailable", "message": f"找不到{town}鄉鎮預報"}
    return {"status": "ok", "data": match}


def places_search(query: str) -> dict[str, Any]:
    import requests

    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        return {"status": "not_configured", "message": "尚未設定 Google Places API"}
    response = requests.post(
        PLACES_URL,
        headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": (
                "places.displayName,places.formattedAddress,places.priceLevel,"
                "places.currentOpeningHours,places.regularOpeningHours,places.googleMapsUri,"
                "places.websiteUri,places.rating"
            ),
        },
        json={"textQuery": query, "languageCode": "zh-TW", "regionCode": "TW", "pageSize": 5},
        timeout=15,
    )
    response.raise_for_status()
    return {"status": "ok", "places": response.json().get("places", [])}


def transport_links(origin: str, destination: str) -> list[dict[str, str]]:
    return [
        {"name": "Google 地圖大眾運輸路線", "url": f"https://www.google.com/maps/dir/?api=1&origin={quote_plus(origin)}&destination={quote_plus(destination)}&travelmode=transit"},
        {"name": "台鐵訂票", "url": "https://www.railway.gov.tw/tra-tip-web/tip/tip001/tip112/gobytime"},
        {"name": "客運路線與票價查詢", "url": "https://www.taiwanbus.tw/"},
    ]


def lodging_links(town: str) -> list[dict[str, str]]:
    return [
        {"name": "Google 住宿搜尋（價格依日期確認）", "url": f"https://www.google.com/travel/hotels/{quote_plus(town)}"},
        {"name": "Booking.com 住宿搜尋", "url": f"https://www.booking.com/searchresults.zh-tw.html?ss={quote_plus(town)}"},
    ]


def format_places(payload: dict[str, Any]) -> str:
    if payload["status"] != "ok":
        return f"資料狀態：{payload['message']}。請使用下方查詢連結確認。"
    lines = []
    for place in payload.get("places", []):
        name = place.get("displayName", {}).get("text", "未提供名稱")
        price = place.get("priceLevel", "未提供價格級距")
        hours = place.get("currentOpeningHours", {}).get("weekdayDescriptions") or place.get("regularOpeningHours", {}).get("weekdayDescriptions")
        hours_text = "；".join(hours) if hours else "未提供營業時間，請致電確認"
        url = place.get("googleMapsUri", "")
        lines.append(f"- {name}｜價格級距：{price}｜營業時間：{hours_text}｜{url}")
    return "\n".join(lines) or "查無結果，請使用查詢連結確認。"


def build_brief(event: dict[str, Any], stage: str, today: date, unsubscribe_url: str = "") -> str:
    start = date.fromisoformat(event["startDate"])
    days_until = (start - today).days
    weather = cwa_forecast(event["town"], event.get("county", "")) if days_until <= 7 else {
        "status": "forecast_window_exceeded",
        "message": f"中央氣象署鄉鎮預報目前不涵蓋 {event['startDate']}；請於出發前7天內再查最新預報。",
    }
    lodging = places_search(f"民宿 住宿 {event['town']}")
    food = places_search(f"在地特色餐廳 {event['town']}")
    origin = os.getenv("TRIP_ORIGIN", "臺北市")
    weather_text = weather["message"] if weather["status"] != "ok" else "已取得中央氣象署鄉鎮預報，出發前請再次確認最新預報。"
    unsubscribe_line = f"取消訂閱：{unsubscribe_url}" if unsubscribe_url else "本信箱為網站管理者設定的固定通知地址。"
    return f"""臺灣原住民族祭典旅遊規劃｜{event.get('name', event['id'])}

祭典日期：{event['startDate']}（距今 {days_until} 天）
地點：{event['location']}
祭儀開放方式：{event.get('accessNote', '請以主辦部落當年公告為準')}
官方公告：{event['sourceUrl']}

天氣：{weather_text}

交通（起點：{origin}；票價依訂票/業者當日頁面確認）：
{chr(10).join('- ' + row['name'] + '：' + row['url'] for row in transport_links(origin, event['location']))}

住宿資料：
{format_places(lodging)}
{chr(10).join('- ' + row['name'] + '：' + row['url'] for row in lodging_links(event['town']))}

沿途餐飲與營業時間：
{format_places(food)}

注意：Places 價格級距不是房價或實際餐費；訂房價格須按日期、人數及房型確認。若預報尚未涵蓋祭期，不能視為已知天氣。
{unsubscribe_line}
"""


def send_email(subject: str, body: str, recipient: str, idempotency_key: str) -> str:
    import requests

    key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("MAIL_FROM")
    if not key or not sender or not recipient:
        raise RuntimeError("尚未設定 RESEND_API_KEY、MAIL_FROM 或 TRIP_EMAIL_TO GitHub Secret")
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
        json={"from": sender, "to": [recipient], "subject": subject, "text": body},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("id", "")


def preview(events_path: Path, today: date) -> list[dict[str, str]]:
    events = load_json(events_path, [])
    return [{"id": event["id"], "name": event.get("name", event["id"]), "stage": stage,
             "startDate": event["startDate"]}
            for event, stage in due_events(events, today)]


def process_claimed(events_path: Path, state_path: Path, today: date,
                    recipient: str) -> tuple[int, int]:
    events = load_json(events_path, [])
    state = load_json(state_path, {})
    by_id = {event.get("id"): event for event in events if event.get("id")}
    success = 0
    failed = 0
    for key, record in list(state.items()):
        if record.get("status") != "claimed":
            continue
        event = by_id.get(record.get("eventId"))
        if not event or event_stage(event, today) != record.get("stage"):
            continue
        target_hash = record.get("recipientHash")
        target = next((row for row in recipients_for_event(event, recipient)
                       if hashlib.sha256(row["email"].encode("utf-8")).hexdigest() == target_hash), None)
        if not target:
            # The visitor unsubscribed after the claim was created; do not send.
            state[key] = {**record, "status": "skipped_unsubscribed",
                          "finishedAt": datetime.now(timezone.utc).isoformat()}
            continue
        stage_label = "出發前天氣更新" if record["stage"] == "weather_update" else "行程規劃"
        try:
            unsubscribe_url = (
                "https://aziza80110.github.io/Indigenous-Fest/subscribe.html?unsubscribe="
                + target["unsubscribe_token"]
            ) if target.get("unsubscribe_token") else ""
            body = build_brief(event, record["stage"], today, unsubscribe_url)
            email_id = send_email(
                f"【祭典{stage_label}】{event.get('name', event['id'])}｜{event['startDate']}",
                body,
                target["email"],
                key,
            )
            state[key] = {**record, "status": "sent", "sentAt": datetime.now(timezone.utc).isoformat(), "emailId": email_id}
            success += 1
            print(f"已寄出行程通知：{event.get('name', event['id'])} ({record['stage']})")
        except Exception as exc:  # record failure to allow a later scheduled retry
            # State lives on a public-repository branch; never persist provider
            # responses or exception text that could contain request details.
            state[key] = {**record, "status": "failed", "failedAt": datetime.now(timezone.utc).isoformat(), "errorType": type(exc).__name__}
            failed += 1
            print(f"寄送失敗：{event.get('name', event['id'])} ({record['stage']})；錯誤：{type(exc).__name__}")
    save_json(state_path, state)
    return success, failed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preview", "claim", "send"))
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--today", help="ISO date for controlled tests, e.g. 2026-11-08")
    args = parser.parse_args()
    today = parse_today(args.today)
    recipient = os.getenv("TRIP_EMAIL_TO", "")

    if args.command == "preview":
        due = preview(args.events, today)
        print(json.dumps({"dueCount": len(due), "events": due}, ensure_ascii=False, indent=2))
        return 0

    events = load_json(args.events, [])
    state = load_json(args.state, {})
    if args.command == "claim":
        keys = claim_due(events, state, today, recipient)
        if keys and not recipient:
            raise RuntimeError("已有符合日期的祭典；請先在 GitHub Actions 設定 TRIP_EMAIL_TO Secret")
        save_json(args.state, state)
        print(f"已建立 {len(keys)} 筆待寄送紀錄。")
        return 0

    success, failed = process_claimed(args.events, args.state, today, recipient)
    print(f"寄送成功 {success} 筆；失敗 {failed} 筆。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
