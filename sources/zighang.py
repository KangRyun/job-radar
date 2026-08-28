"""직행(zighang) IT 신입/인턴 공고 수집.

GET api.zighang.com/api/recruitments — 인증·차단 없음 (2026-08-26 검증).
경력 필터가 '범위 겹침' 방식이라 신입(careerMax=0)과 인턴(employeeTypes)을
한 쿼리로 못 묶는다 → 두 번 조회 후 id로 병합.

참고: api.zighang.com의 robots.txt는 크롤러 배제를 명시하고 있다.
웹사이트 프론트가 쓰는 것과 동일한 공개 API를 호출한다. 평시에는 쿼리당
1페이지(size=100)만 읽어 시간당 2회 수준을 유지하고, 백필 때만 깊게 넘긴다.
"""
import datetime as dt

import requests

from . import _http

API = "https://api.zighang.com/api/recruitments"
DEPTH_ONES = ["IT_개발", "AI_데이터"]  # 게임 직군도 원하면 "게임" 추가

PAGE_SIZE = 100  # size=200은 API가 null을 반환한다

COMMON = [
    ("size", str(PAGE_SIZE)),
    ("sortCondition", "LATEST"),
    ("orderCondition", "DESC"),
] + [("depthOnes", d) for d in DEPTH_ONES]

QUERIES = {
    # 신입: 경력 0~0 & 경력무관 제외
    "신입": COMMON + [("careerMin", "0"), ("careerMax", "0"), ("includeCareerOpen", "false")],
    # 인턴: 고용형태로 필터 (경력 제약 없음)
    "인턴": COMMON + [("employeeTypes", "체험형인턴"), ("employeeTypes", "전환형인턴")],
}


def fetch(since: dt.date | None = None, max_pages: int = 20) -> list[dict]:
    """등록일(createdAt) 내림차순이므로, 페이지의 가장 오래된 항목이 since보다
    과거면 이후 페이지는 볼 필요가 없다. since=None이면 1페이지만 읽는다."""
    by_id: dict[str, dict] = {}
    for params in QUERIES.values():
        for page in range(max_pages):
            res = _http.get(API, params=params + [("page", str(page))])
            res.raise_for_status()
            data = res.json()["data"]
            content = data["content"]
            _collect(by_id, content)
            if not content or page + 1 >= data.get("totalPages", 1):
                break
            if since is None:
                break
            oldest = min((i.get("createdAt") or "") for i in content)
            if oldest[:10] < since.isoformat():
                break
    return list(by_id.values())


def _collect(by_id: dict, content: list) -> None:
    for item in content:
        if item["id"] in by_id:
            continue
        role = item.get("title") or "-"
        depth_twos = ", ".join(item.get("depthTwos") or [])
        if depth_twos:
            role = f"{role} ({depth_twos})"
        end_date = item.get("endDate")
        by_id[item["id"]] = {
            "id": f"zighang-{item['id']}",
            "source": "직행",
            "company": (item.get("company") or {}).get("name") or "(기업명 없음)",
            "role": role,
            "link": f"https://zighang.com/recruitment/{item['id']}",
            "deadline": end_date[:10] if end_date else None,
            "posted": (item.get("createdAt") or "")[:10] or None,
        }
