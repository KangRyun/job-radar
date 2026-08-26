"""직행(zighang)·자소설닷컴에서 IT 신입/인턴 공고를 수집해 Notion DB에 삽입.

새 행이 들어가면 notify.py(10분 주기)가 자동으로 Mattermost @all 알림을 보내므로
여기서는 '수집 → 중복 제거 → Notion 삽입'만 담당한다.

주의: Notion Integration에 'Insert content' 권한이 켜져 있어야 한다.
"""
import json
import os
import pathlib

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ["NOTION_DATA_SOURCE_ID"]

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

STATE_PATH = pathlib.Path("crawl_state.json")
KEEP_IDS = 2000  # 출처별로 보관할 최근 공고 ID 수
MAX_INSERT_PER_RUN = 20  # 폭주 방지: 한 번에 넣는 최대 건수


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"seen": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def insert_notion(job: dict) -> None:
    """job: {id, source, company, role, link, deadline(YYYY-MM-DD | None)}"""
    props = {
        "기업명": {"title": [{"text": {"content": job["company"][:200]}}]},
        "직무": {"rich_text": [{"text": {"content": f"[{job['source']}] {job['role']}"[:200]}}]},
        "링크": {"url": job["link"]},
    }
    if job.get("deadline"):
        props["마감일"] = {"date": {"start": job["deadline"]}}

    res = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json={
            "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
            "properties": props,
        },
        timeout=30,
    )
    res.raise_for_status()


def run_source(name: str, fetch, state: dict) -> int:
    """fetch() → 최신 공고 리스트. 새 것만 Notion에 넣고 넣은 건수 반환."""
    seen_list = state["seen"].setdefault(name, [])
    seen = set(seen_list)
    first_run = not seen_list  # 최초 실행이면 기존 공고를 쏟아붓지 않고 기준선만 잡는다

    try:
        jobs = fetch()
    except Exception as e:  # 한 소스가 죽어도 다른 소스는 계속
        print(f"[{name}] 수집 실패: {e}")
        return 0

    inserted = 0
    for job in jobs:
        if job["id"] in seen:
            continue
        if not first_run and inserted < MAX_INSERT_PER_RUN:
            insert_notion(job)
            inserted += 1
        seen_list.append(job["id"])
        seen.add(job["id"])

    state["seen"][name] = seen_list[-KEEP_IDS:]
    tag = "기준선 설정" if first_run else f"신규 {inserted}건 삽입"
    print(f"[{name}] 조회 {len(jobs)}건 / {tag}")
    return inserted


def main() -> None:
    # 각 소스 모듈은 fetch() 함수 하나만 제공하면 된다
    import sources.jasoseol
    import sources.zighang

    state = load_state()
    total = 0
    total += run_source("직행", sources.zighang.fetch, state)
    total += run_source("자소설", sources.jasoseol.fetch, state)
    save_state(state)
    print(f"총 {total}건 Notion에 추가")


if __name__ == "__main__":
    main()
