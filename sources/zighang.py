"""직행(zighang) IT 신입/인턴 공고 수집.

GET api.zighang.com/api/recruitments — 인증·차단 없음 (2026-08-26 검증).
경력 필터가 '범위 겹침' 방식이라 신입(careerMax=0)과 인턴(employeeTypes)을
한 쿼리로 못 묶는다 → 두 번 조회 후 id로 병합.

참고: api.zighang.com의 robots.txt는 크롤러 배제를 명시하고 있다.
웹사이트 프론트가 쓰는 것과 동일한 공개 API를 시간당 2회 저부하로 호출한다.
"""
import requests

API = "https://api.zighang.com/api/recruitments"
DEPTH_ONES = ["IT_개발", "AI_데이터"]  # 게임 직군도 원하면 "게임" 추가

COMMON = [
    ("page", "0"),
    ("size", "30"),
    ("sortCondition", "LATEST"),
    ("orderCondition", "DESC"),
] + [("depthOnes", d) for d in DEPTH_ONES]

QUERIES = {
    # 신입: 경력 0~0 & 경력무관 제외
    "신입": COMMON + [("careerMin", "0"), ("careerMax", "0"), ("includeCareerOpen", "false")],
    # 인턴: 고용형태로 필터 (경력 제약 없음)
    "인턴": COMMON + [("employeeTypes", "체험형인턴"), ("employeeTypes", "전환형인턴")],
}


def fetch() -> list[dict]:
    by_id: dict[str, dict] = {}
    for params in QUERIES.values():
        res = requests.get(API, params=params, timeout=30,
                           headers={"Accept": "application/json"})
        res.raise_for_status()
        for item in res.json()["data"]["content"]:
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
            }
    return list(by_id.values())
