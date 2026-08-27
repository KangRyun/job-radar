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
QUIET_START = 19  # KST 19:00부터
QUIET_END = 8     # KST 08:00까지는 발송 보류 → 08:00 첫 실행에서 일괄 발송
MAX_BULK_LINES = 25  # 일괄 발송 메시지에 표시할 최대 줄 수


# --- 시간 유틸 -------------------------------------------------------------
def is_quiet_hours() -> bool:
    """야간이면 True. 무음 중에는 state를 저장하지 않으므로 last_checked가 그대로
    남고, 아침 첫 실행이 그 사이 생긴 행을 전부 조회해 한 메시지로 묶어 보낸다."""
    if os.environ.get("FORCE_SEND", "").lower() in ("1", "true", "yes"):
        return False  # 수동 실행(workflow_dispatch)은 야간에도 즉시 발송
    hour = dt.datetime.now(KST).hour
    return hour >= QUIET_START or hour < QUIET_END


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


def md_escape(s: str) -> str:
    """링크 텍스트용. 크롤러 행의 직무는 [사람인] 처럼 대괄호로 시작해서
    이스케이프하지 않으면 마크다운 링크가 깨진다."""
    return s.replace("[", "\\[").replace("]", "\\]")


def build_bulk_payload(pages: list[dict]) -> dict:
    """여러 건(주로 야간 대기분)을 한 메시지로 묶는다. 건별로 쏘면 도배가 된다.
    마감 임박순으로 정렬 — 아침에 위에서부터 읽으면 급한 것부터 보인다."""
    def due_key(page: dict) -> str:
        return plain(page["properties"].get("마감일")) or "9999-12-31"

    lines = []
    for page in sorted(pages, key=due_key):
        props = page["properties"]
        company = plain(props.get("기업명")) or "(기업명 없음)"
        role = plain(props.get("직무")) or "-"
        link = plain(props.get("링크")) or page["url"]
        due = due_text(plain(props.get("마감일")))
        lines.append(f"- **{due}** · [{md_escape(company)} · {md_escape(role)}]({link})")

    if len(lines) > MAX_BULK_LINES:
        dropped = len(lines) - MAX_BULK_LINES
        lines = lines[:MAX_BULK_LINES] + [f"…외 {dropped}건 (Notion에서 확인)"]

    return {
        "username": "채용공고봇",
        "icon_emoji": ":briefcase:",
        "text": f"@all 새 채용공고 {len(pages)}건이 등록되었습니다.",
        "attachments": [
            {
                "color": "#2E7CF6",
                "text": "\n".join(lines),
                "footer": "Notion · 채용공고 리스트업",
            }
        ],
    }


def send(payload: dict) -> None:
    res = requests.post(MM_WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()


# --- 엔트리포인트 ----------------------------------------------------------
def main() -> None:
    if is_quiet_hours():
        print(f"야간 무음 ({QUIET_START}:00~0{QUIET_END}:00 KST) — 0{QUIET_END}:00에 일괄 발송")
        return

    state = load_state()
    notified = state.get("notified", [])
    seen = set(notified)
    pending = [p for p in state.get("pending", []) if p not in seen]
    ready: list[dict] = []  # 이번 실행에서 보낼 페이지 (야간 누적분 포함)

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
            ready.append(page)
            seen.add(pid)
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
            ready.append(page)
            seen.add(page["id"])
        else:
            still_pending.append(page["id"])  # 4개 필드가 채워지면 다음 실행에서 발송

    # 3) 발송 — 1건이면 카드, 여러 건이면 목록 한 장. 실패 시 state를 저장하지
    #    않으므로 다음 실행에서 통째로 재시도된다(부분 발송 없음).
    if ready:
        send(build_payload(ready[0]) if len(ready) == 1 else build_bulk_payload(ready))
        notified.extend(p["id"] for p in ready)

    state["last_checked"] = iso_z(latest)
    state["notified"] = notified[-KEEP_IDS:]
    state["pending"] = still_pending
    save_state(state)
    print(
        f"조회 {len(pages)}건 / 발송 {len(ready)}건 / 입력대기 {len(still_pending)}건"
        f" / 기준시각 {state['last_checked']}"
    )


if __name__ == "__main__":
    main()
