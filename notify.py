import datetime as dt
import json
import os
import pathlib

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]
MM_WEBHOOK_URL = os.environ["MM_WEBHOOK_URL"]

NOTION_API = f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

STATE_PATH = pathlib.Path("state.json")
KST = dt.timezone(dt.timedelta(hours=9))
KEEP_IDS = 500  # 중복 방지용으로 보관할 최근 page_id 개수
REQUIRED_PROPS = ("기업명", "직무", "링크")  # 마감일 포함 4개가 채워져야 알림
PENDING_DAYS = 7  # 미완성 행을 재확인하는 최대 기간


# --- 시간 유틸 -------------------------------------------------------------
def iso_z(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# --- 상태 관리 -------------------------------------------------------------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    # 최초 실행: 과거 데이터를 전부 쏘지 않도록 '지금'을 기준선으로 잡는다
    return {"last_checked": iso_z(dt.datetime.now(dt.timezone.utc)), "notified": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- Notion 조회 -----------------------------------------------------------
def fetch_new_pages(after_iso: str) -> list[dict]:
    # Notion은 created_time을 분 단위로 절삭해 반환하므로, 기준시각 그대로 필터하면
    # 같은 분에 생성된 행이 빠진다. 2분 버퍼를 두고 중복은 notified ID로 걸러낸다.
    buffered = iso_z(parse_iso(after_iso) - dt.timedelta(minutes=2))
    results, cursor = [], None
    while True:
        body = {
            "filter": {"timestamp": "created_time", "created_time": {"on_or_after": buffered}},
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor

        res = requests.post(NOTION_API, headers=HEADERS, json=body, timeout=30)
        res.raise_for_status()
        data = res.json()

        results.extend(data["results"])
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def get_page(page_id: str) -> dict | None:
    res = requests.get(
        f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS, timeout=30
    )
    if res.status_code == 404:  # 삭제된 행
        return None
    res.raise_for_status()
    return res.json()


def is_complete(page: dict) -> bool:
    """기업명·직무·링크·마감일이 모두 채워졌는지. 크롤러가 넣은 행([출처] 태그)은
    상시채용 등 마감일이 없을 수 있어 마감일을 예외로 둔다."""
    props = page["properties"]
    if not all(plain(props.get(k)) for k in REQUIRED_PROPS):
        return False
    if plain(props.get("마감일")):
        return True
    return plain(props.get("직무")).startswith("[")


def plain(prop: dict | None) -> str:
    if not prop:
        return ""
    kind = prop["type"]
    if kind == "title":
        return "".join(t["plain_text"] for t in prop["title"])
    if kind == "rich_text":
        return "".join(t["plain_text"] for t in prop["rich_text"])
    if kind == "url":
        return prop.get("url") or ""
    if kind == "date":
        return (prop.get("date") or {}).get("start") or ""
    return ""


# --- 메시지 조립 -----------------------------------------------------------
def due_text(due: str) -> str:
    if not due:
        return "미정"
    try:
        d = dt.date.fromisoformat(due[:10])
    except ValueError:
        return due
    left = (d - dt.datetime.now(KST).date()).days
    if left < 0:
        return f"{d} (마감)"
    return f"{d} (D-{left})" if left else f"{d} (D-DAY)"


def build_payload(page: dict) -> dict:
    props = page["properties"]
    company = plain(props.get("기업명")) or "(기업명 없음)"
    role = plain(props.get("직무")) or "-"
    link = plain(props.get("링크")) or page["url"]

    attachment = {
        "color": "#2E7CF6",
        "title": f"{company} · {role}",
        "title_link": link,
        "fields": [
            {"short": True, "title": "직무", "value": role},
            {"short": True, "title": "마감일", "value": due_text(plain(props.get("마감일")))},
        ],
        "footer": "Notion · 채용공고 리스트업",
    }
    return {
        "username": "채용공고봇",
        "icon_emoji": ":briefcase:",
        "text": "@all 새 채용공고가 등록되었습니다.",
        "attachments": [attachment],
    }


def send(payload: dict) -> None:
    res = requests.post(MM_WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()


# --- 엔트리포인트 ----------------------------------------------------------
def main() -> None:
    state = load_state()
    notified = state.get("notified", [])
    seen = set(notified)
    pending = [p for p in state.get("pending", []) if p not in seen]
    sent = 0

    # 1) 지난 실행에서 미완성이었던 행 재확인
    still_pending = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=PENDING_DAYS)
    for pid in pending:
        page = get_page(pid)
        if page is None or page.get("in_trash") or page.get("archived"):
            continue  # 삭제됨 → 포기
        if parse_iso(page["created_time"]) < cutoff:
            continue  # 너무 오래 방치됨 → 포기
        if is_complete(page):
            send(build_payload(page))
            notified.append(pid)
            seen.add(pid)
            sent += 1
        else:
            still_pending.append(pid)

    # 2) 새로 생성된 행 확인
    pages = fetch_new_pages(state["last_checked"])
    latest = parse_iso(state["last_checked"])
    for page in pages:
        latest = max(latest, parse_iso(page["created_time"]))
        if page["id"] in seen or page["id"] in still_pending:
            continue
        if is_complete(page):
            send(build_payload(page))
            notified.append(page["id"])
            seen.add(page["id"])
            sent += 1
        else:
            still_pending.append(page["id"])  # 4개 필드가 채워지면 다음 실행에서 발송

    state["last_checked"] = iso_z(latest)
    state["notified"] = notified[-KEEP_IDS:]
    state["pending"] = still_pending
    save_state(state)
    print(
        f"조회 {len(pages)}건 / 발송 {sent}건 / 입력대기 {len(still_pending)}건"
        f" / 기준시각 {state['last_checked']}"
    )


if __name__ == "__main__":
    main()
