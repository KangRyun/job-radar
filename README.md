# job-radar 📡

채용 사이트(직행·자소설닷컴·인디스워크)에서 IT 신입/인턴 공고를 자동 수집해 **Notion 데이터베이스**에 쌓고, 새 공고가 등록되면 **Mattermost**로 알림을 보내는 봇.
서버 없이 **GitHub Actions**만으로 동작한다.

```mermaid
flowchart LR
    A[직행 API] -->|매시 7분| C[crawler.py]
    B[자소설닷컴 API] -->
    B2[인디스워크 API] -->|매시 7분| C
    C -->|신규 공고 삽입| D[(Notion DB\n채용공고 리스트업)]
    E[수동 입력] --> D
    D -->|10분마다 폴링| F[notify.py]
    D -->|09:00 / 13:00 KST| G[digest.py]
    F -->|@all 새 공고 알림| H[Mattermost 채널]
    G -->|진행 중 공고 목록| H
```

## 구성 요소

| 스크립트 | 워크플로 | 스케줄 | 역할 |
| --- | --- | --- | --- |
| `crawler.py` | `crawler.yml` | 매시 7분 | 직행·자소설닷컴·인디스워크에서 IT 신입/인턴 공고 수집 → Notion DB 삽입 (`직무` 칸에 `[직행]`/`[자소설]`/`[인디스워크]` 출처 표시) |
| `notify.py` | `notify.yml` | 10분마다 | DB에 새로 생긴 행을 감지해 @all 알림. **기업명·직무·링크·마감일 4개 필드가 모두 채워진 행만** 발송하고, 미완성 행은 채워질 때까지 대기(최대 7일) |
| `digest.py` | `digest.yml` | 매일 09:00 / 13:00 KST | 마감이 지나지 않은(진행 중) 공고 전체를 마감 임박 순으로 발송 |

수동으로 행을 추가해도 크롤러가 넣은 행과 똑같이 알림이 나간다. Notion DB가 유일한 관리 지점이다.

## 알림 예시

**새 공고 (즉시, @all)**
> 💼 **채용공고봇** — @all 새 채용공고가 등록되었습니다.
> ┃ **STX엔진 · [자소설] 2026 신입/경력 수시채용 (서버운용)**
> ┃ 직무: … 마감일: 2026-09-08 (D-13)

**데일리 다이제스트 (하루 2회, 멘션 없음)**
> 📅 **채용공고봇** — 📋 2026-08-26 기준 진행 중인 채용공고 19건
> ┃ **2026-08-28 (D-2)** · 회사명 · 직무 …

## 설정 방법

### 1. Notion

1. [my-integrations](https://www.notion.so/my-integrations)에서 Internal Integration 생성 → `ntn_` 토큰 확보
2. Capabilities: **콘텐츠 읽기 + 콘텐츠 삽입** 필수 (업데이트는 선택)
3. 대상 페이지 `···` → 연결 → Integration 추가 (빠뜨리면 API가 404 반환)
4. DB에 필요한 속성: `기업명`(제목) · `직무`(텍스트) · `링크`(URL) · `마감일`(날짜)

### 2. Mattermost

통합 → 유입 웹후크 → 웹후크 추가(채널 잠금 권장) → URL 확보

### 3. GitHub Secrets

Settings → Secrets and variables → Actions에 등록:

| Name | Value |
| --- | --- |
| `NOTION_TOKEN` | `ntn_...` |
| `NOTION_DATA_SOURCE_ID` | Notion DB의 데이터 소스 ID (`···` → 데이터 소스 관리에서 복사) |
| `MM_WEBHOOK_URL` | `https://<서버>/hooks/...` |

등록 후 Actions 탭에서 각 워크플로를 **Run workflow**로 1회 수동 실행해 확인한다.

## 로컬 개발

```bash
pip install requests
cp .env.example .env          # 값 채우기 (.env는 gitignore됨)
set -a; source .env; set +a
python crawler.py             # 최초 실행: 기준선만 설정, 삽입 0건
python notify.py              # 최초 실행: state.json 생성, 발송 0건
python digest.py              # 진행 중 공고 목록 즉시 발송
```

## 상태 파일 (커밋 대상)

- `state.json` — notify의 마지막 확인 시각 + 발송한 행 ID + 입력 대기 중인 행 ID
- `crawl_state.json` — 크롤러가 이미 본 출처별 공고 ID (최초 실행 시 기존 공고를 쏟아붓지 않기 위한 기준선)

두 파일은 워크플로가 자동 커밋한다. **`.gitignore`에 넣으면 안 된다.**

## 커스터마이즈 포인트

| 바꾸고 싶은 것 | 위치 |
| --- | --- |
| 수집 직군 (예: 게임 추가) | `sources/zighang.py`의 `DEPTH_ONES`, `sources/jasoseol.py`의 `IT_DUTY_IDS`, `sources/inthiswork.py`의 `TAGS_IT` |
| @all 멘션 끄기 | `notify.py` `build_payload()`의 `text` |
| 다이제스트 시간 | `digest.yml`의 cron (UTC 기준, KST−9시간) |
| 알림 필수 필드 | `notify.py`의 `REQUIRED_PROPS` |
| 회당 최대 삽입 건수 | `crawler.py`의 `MAX_INSERT_PER_RUN` |

## 운영 주의사항

- **cron은 UTC.** `0 0 * * *` = KST 09:00. 무료 러너는 혼잡 시 수 분~수십 분 지연될 수 있다
- **60일 규칙**: 커밋이 60일간 없으면 스케줄이 자동 비활성화되지만, 이 구성은 상태 파일을 계속 커밋하므로 자연 해결됨
- **웹훅 URL은 비밀**: URL만 알면 누구나 채널에 글을 쓸 수 있다. 리포는 Private 유지, 값은 Secrets로만
- Notion API 버전 `2025-09-03` (data_sources 엔드포인트) 고정
- Notion은 `created_time`을 분 단위로 절삭해 반환한다 — notify는 2분 버퍼 + ID 중복 제거로 대응
- 직행 API 호스트의 robots.txt는 크롤러 배제를 명시하고 있다. 이 봇은 웹 프론트가 쓰는 것과 동일한 공개 API를 시간당 2회만 호출하는 저부하 개인용이지만, 운영자 요청이 있으면 `crawler.py`에서 해당 소스를 제외할 것

## 확장 아이디어

- 마감 D-3 임박 공고만 골라 🚨 리마인드
- `자소서 문항1`이 비어 있는데 마감 임박한 공고 경고
- 관심 키워드(AI, 데이터 등) 포함 공고에 ⭐ 하이라이트
- `last_edited_time` 기반 공고 수정 감지
