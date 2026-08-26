"""자소설닷컴 IT 신입/인턴 공고 수집.

GET /api/v1/employment_companies — 인증 불필요, robots.txt 허용 (2026-08-26 검증).
회사 단위 응답이라 비IT 직무가 섞일 수 있어 employments를 클라이언트에서 재확인한다.
"""
import datetime as dt

import requests

API = "https://jasoseol.com/api/v1/employment_companies"

# 대분류 94(IT·인터넷)의 중분류 id — 필터는 중분류만 동작함
IT_DUTY_IDS = {160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171}
# 1=신입, 3=인턴, 5=신입/경력
TARGET_DIVISIONS = {1, 3, 5}

KST = dt.timezone(dt.timedelta(hours=9))


def fetch() -> list[dict]:
    today = dt.datetime.now(KST).date().isoformat()
    params = [
        ("per_page", "30"),
        ("page", "1"),
        ("by_division[]", "1"),
        ("by_division[]", "3"),
        ("by_duty_group_ids", ",".join(str(i) for i in sorted(IT_DUTY_IDS))),
        ("after_end_time", today),
    ]
    res = requests.get(API, params=params, timeout=30,
                       headers={"Accept": "application/json"})
    res.raise_for_status()

    jobs = []
    for item in res.json():
        matched = [
            e for e in item.get("employments", [])
            if set(e.get("duty_group_ids") or []) & IT_DUTY_IDS
            and set(e.get("division") or []) & TARGET_DIVISIONS
        ]
        if not matched:
            continue

        fields = ", ".join(dict.fromkeys(e["field"] for e in matched if e.get("field")))
        role = item.get("title") or "-"
        if fields:
            role = f"{role} ({fields})"

        end_time = item.get("end_time")
        jobs.append({
            "id": f"jasoseol-{item['id']}",
            "source": "자소설",
            "company": item.get("name") or "(기업명 없음)",
            "role": role,
            "link": f"https://jasoseol.com/recruit/{item['id']}",
            "deadline": end_time[:10] if end_time else None,
        })
    return jobs
