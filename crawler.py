"""직행(zighang)·자소설닷컴에서 IT 신입/인턴 공고를 수집해 Notion DB에 삽입.

새 행이 들어가면 notify.py(30분 주기)가 자동으로 Mattermost @all 알림을 보내므로
여기서는 '수집 → 중복 제거 → Notion 삽입'만 담당한다.

주의: Notion Integration에 'Insert content' 권한이 켜져 있어야 한다.
"""
import datetime as dt
import json
import os
import pathlib
import re
import time

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
# 폭주 방지: 한 번에 넣는 최대 건수. 백필 때만 크게 올린다.
MAX_INSERT_PER_RUN = int(os.environ.get("MAX_INSERT_PER_RUN", "20"))
# 등록일이 이보다 오래된 공고는 수집하지 않는다. 세 소스 모두 마감 지난 공고를
# 반환하지 않아 마감일 필터는 무의미하고(측정 결과 과거 마감 0건), 부피의 76%가
# 마감일 없는 상시모집이라 실질적인 커트라인은 등록일이다.
RECENT_DAYS = int(os.environ.get("RECENT_DAYS", "14"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "20"))
KST = dt.timezone(dt.timedelta(hours=9))


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

    body = {
        "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
        "properties": props,
    }
    for attempt in range(5):
        res = requests.post("https://api.notion.com/v1/pages",
                            headers=HEADERS, json=body, timeout=30)
        if res.status_code == 429:  # Notion 레이트리밋 — Retry-After를 지킨다
            time.sleep(float(res.headers.get("Retry-After") or 2 ** attempt))
            continue
        res.raise_for_status()
        return
    res.raise_for_status()


def norm_role(role: str) -> str:
    """직무명을 교차중복 비교용으로 정규화. 사이트마다 표기가 달라 완벽하지 않다."""
    return re.sub(r"[^0-9a-z가-힣]", "", role.lower())[:24]


def cutoff() -> dt.date:
    """삽입 대상 커트라인(고정). 이보다 오래 등록된 공고는 넣지 않는다."""
    return dt.datetime.now(KST).date() - dt.timedelta(days=RECENT_DAYS)


def scan_since(state: dict) -> dt.date:
    """조회 커트라인(가변). 평시에는 지난 실행 하루 전까지만 훑어 페이지 수를
    줄이고, 오래 멈춰 있었으면 cutoff()까지 거슬러 올라간다. 고정 커트라인으로
    매번 훑으면 직행 기준 17페이지 x 2쿼리를 실행마다 받게 된다."""
    last = state.get("last_crawled")
    if not last:
        return cutoff()
    try:
        soft = dt.date.fromisoformat(last[:10]) - dt.timedelta(days=1)
    except ValueError:
        return cutoff()
    return max(cutoff(), soft)


def is_recent(job: dict) -> bool:
    """등록일이 커트라인 이내인가. 등록일을 모르면 버리지 않고 넣는다."""
    posted = job.get("posted")
    if not posted:
        return True
    try:
        return dt.date.fromisoformat(posted[:10]) >= cutoff()
    except ValueError:
        return True


def cross_key(job: dict) -> str | None:
    """같은 공고가 여러 사이트에 올라오는 경우를 잡기 위한 교차 중복 키.

    마감일이 없는 공고는 오탐(같은 회사의 다른 상시공고) 위험이 커서 제외.
    직무까지 넣는 이유: 회사+마감일만 쓰면 한 회사가 같은 날 마감되는 여러
    자리를 올렸을 때 하나만 남고 나머지가 조용히 버려진다(어플라이드
    머티어리얼즈 MesoVision/Metrology 두 자리가 실제로 이렇게 유실됐다).
    직무 표기가 사이트마다 달라 교차중복 탐지력은 떨어지지만, 보이지 않는
    유실보다 눈에 보이는 중복이 낫다.
    """
    if not job.get("deadline"):
        return None
    company = job["company"].replace(" ", "")
    return f"{company}|{job['deadline']}|{norm_role(job['role'])}"


def run_source(name: str, fetch, state: dict, since: dt.date) -> int:
    """fetch() → 최신 공고 리스트. 새 것만 Notion에 넣고 넣은 건수 반환.

    seen에는 '실제로 처리를 끝낸' 공고만 기록한다. 캡에 걸려 못 넣은 건이나
    삽입이 실패한 건은 기록하지 않아 다음 실행이 그대로 이어서 처리한다.
    """
    seen_list = state["seen"].setdefault(name, [])
    seen = set(seen_list)
    xkeys = state.setdefault("cross_keys", [])
    xkey_set = set(xkeys)

    try:
        fetched = fetch(since, MAX_PAGES)
    except Exception as e:  # 한 소스가 죽어도 다른 소스는 계속
        print(f"[{name}] 수집 실패: {e}")
        return 0
    jobs = [j for j in fetched if is_recent(j)]
    old = len(fetched) - len(jobs)

    def mark(job_id: str) -> None:
        seen_list.append(job_id)
        seen.add(job_id)

    inserted = deferred = 0
    stopped = ""
    for job in jobs:
        key = cross_key(job)
        if job["id"] in seen:
            if key is not None and key not in xkey_set:
                xkeys.append(key)  # 기존 공고의 키도 축적해 타 사이트 중복을 차단
                xkey_set.add(key)
            continue

        if key is not None and key in xkey_set:
            mark(job["id"])  # 타 사이트에 이미 올라온 공고 — 넣지 않고 처리 완료
            continue

        if inserted >= MAX_INSERT_PER_RUN:
            deferred += 1  # seen에 넣지 않는다 → 다음 실행이 이어서 삽입
            continue

        try:
            insert_notion(job)
        except Exception as e:
            # 지금까지 넣은 건 seen에 남아 있으므로 중복 삽입되지 않는다.
            stopped = f" / 삽입 실패로 중단({job['company']}: {e})"
            break

        inserted += 1
        mark(job["id"])
        if key is not None:
            xkeys.append(key)
            xkey_set.add(key)

    state["seen"][name] = seen_list[-KEEP_IDS:]
    state["cross_keys"] = xkeys[-KEEP_IDS:]
    rest = f" / 다음 실행 대기 {deferred}건" if deferred else ""
    gone = f" / {RECENT_DAYS}일 초과 {old}건 제외" if old else ""
    print(f"[{name}] 조회 {len(fetched)}건{gone} / 신규 {inserted}건 삽입{rest}{stopped}")
    return inserted


def main() -> None:
    # 각 소스 모듈은 fetch() 함수 하나만 제공하면 된다
    import sources.jasoseol
    import sources.zighang

    state = load_state()
    since = scan_since(state)
    total = 0
    try:
        total += run_source("직행", sources.zighang.fetch, state, since)
        total += run_source("자소설", sources.jasoseol.fetch, state, since)
        state["last_crawled"] = dt.datetime.now(KST).date().isoformat()
    finally:
        # 중간에 죽어도 이미 삽입한 건은 기록해야 다음 실행이 중복을 만들지 않는다
        save_state(state)
    print(f"총 {total}건 Notion에 추가 (조회 커트라인 {since})")


if __name__ == "__main__":
    main()
