"""소스 공통 HTTP 호출. 429/5xx는 Retry-After를 지켜 재시도한다.

백필처럼 여러 페이지를 연속으로 받으면 사이트가 레이트리밋을 건다
(인디스워크가 실제로 page=2에서 429를 반환했다).
"""
import time

import requests

RETRIES = 4


def get(url: str, *, params=None, timeout: int = 30) -> requests.Response:
    last = None
    for attempt in range(RETRIES):
        res = requests.get(url, params=params, timeout=timeout,
                           headers={"Accept": "application/json"})
        if res.status_code == 429 or res.status_code >= 500:
            last = res
            time.sleep(float(res.headers.get("Retry-After") or 2 ** attempt))
            continue
        return res
    return last if last is not None else res
