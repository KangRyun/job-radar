"""인디스워크(inthiswork.com) IT 신입/인턴 공고 수집.

WordPress REST API — 인증 불필요, robots.txt 허용 (2026-08-26 검증).
title이 "기업명｜공고제목" 형식(전각 파이프 U+FF5C)이라 split해서 쓴다.
"""
import html

import requests

API = "https://inthiswork.com/wp-json/wp/v2/posts"

CAT_ENTRY_INTERN = "191700167"  # 신입/인턴
TAGS_IT = "191700187,191700191"  # IT개발, 데이터분석 (게임=191700302 원하면 추가)
TAG_OPEN_ENDED = 191700261  # 채용시마감 — 마감일이 형식적 원거리 날짜라 무시해야 함


def fetch() -> list[dict]:
    params = {
        "categories": CAT_ENTRY_INTERN,
        "tags": TAGS_IT,
        "orderby": "date",
        "order": "desc",
        "per_page": "20",
        "_fields": "id,link,title,tags,publishpress_future_action",
    }
    res = requests.get(API, params=params, timeout=30,
                       headers={"Accept": "application/json"})
    res.raise_for_status()

    jobs = []
    for item in res.json():
        title = html.unescape(item["title"]["rendered"])
        company, _, role = title.partition("｜")
        if not role:  # 파이프가 없는 제목이면 통짜로 사용
            company, role = "(기업명 없음)", title

        deadline = None
        future = item.get("publishpress_future_action") or {}
        if future.get("enabled") and TAG_OPEN_ENDED not in (item.get("tags") or []):
            deadline = (future.get("date") or "")[:10] or None

        jobs.append({
            "id": f"inthiswork-{item['id']}",
            "source": "인디스워크",
            "company": company.strip(),
            "role": role.strip(),
            "link": item["link"],
            "deadline": deadline,
        })
    return jobs
