"""매일 아침 실행: 마감일이 지나지 않은(기간 내) 채용공고 목록을 Mattermost로 발송."""
import datetime as dt

import requests

from notify import HEADERS, KST, MM_WEBHOOK_URL, NOTION_API, due_text, is_quiet_hours, plain

MAX_LINES = 30  # 메시지가 너무 길어지지 않도록 상한


def fetch_open_pages(today: str) -> list[dict]:
    results, cursor = [], None
    while True:
        body = {
            "filter": {
                "or": [
                    {"property": "마감일", "date": {"on_or_after": today}},
                    {"property": "마감일", "date": {"is_empty": True}},
                ]
            },
            "sorts": [{"property": "마감일", "direction": "ascending"}],
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


def line(page: dict) -> tuple[str, str]:
    """(마감일 유무 구분, 표시 줄) 반환"""
    props = page["properties"]
    company = plain(props.get("기업명")) or "(기업명 없음)"
    role = plain(props.get("직무")) or "-"
    link = plain(props.get("링크")) or page["url"]
    due = plain(props.get("마감일"))
    label = due_text(due)
    if due:
        return "due", f"- **{label}** · [{company} · {role}]({link})"
    return "tbd", f"- [{company} · {role}]({link})"


def main() -> None:
    if is_quiet_hours():
        print("야간 무음 (19:00~08:00 KST) — 건너뜀")
        return

    today = dt.datetime.now(KST).date()
    pages = fetch_open_pages(today.isoformat())

    if not pages:
        print("진행 중 공고 0건 — 발송 생략")
        return

    dated, tbd = [], []
    for p in pages:
        kind, text = line(p)
        (dated if kind == "due" else tbd).append(text)

    lines = dated
    if tbd:
        lines += ["", "**마감일 미정**"] + tbd
    dropped = 0
    if len(lines) > MAX_LINES:
        dropped = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES] + [f"…외 {dropped}건 (Notion에서 확인)"]

    payload = {
        "username": "채용공고봇",
        "icon_emoji": ":calendar:",
        "text": f"📋 **{today} 기준 진행 중인 채용공고 {len(pages)}건**",
        "attachments": [
            {
                "color": "#12B76A",
                "text": "\n".join(lines),
                "footer": "Notion · 채용공고 리스트업 · 매일 09:00 / 13:00 KST",
            }
        ],
    }
    res = requests.post(MM_WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()
    print(f"진행 중 {len(pages)}건 발송 완료 (마감일 있음 {len(dated)} / 미정 {len(tbd)})")


if __name__ == "__main__":
    main()
