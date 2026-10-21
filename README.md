# AI 브리프

국내외 AI 뉴스를 원문과 함께 읽는 한국어 큐레이션 사이트. **GitHub Actions가 6시간마다 뉴스를 수집하고, 매일 오전 6:30(KST)에 일일 브리핑을 만듭니다. Cloudflare Pages가 정적 프런트를 제공합니다.** 상시 서버, Docker, Workers, DB는 필요하지 않습니다.

```text
GitHub Actions
  KST 00/06/12/18시: Python 수집 → 필터·중복 제거 → 영어 기사 한국어 요약(최대 10건)
  KST 06:30: 전날 06:00~당일 06:00 자료 → OpenRouter 일일 브리핑 1회
  각 작업: JSON 검증 → 변경된 뉴스·브리핑 JSON을 한 번 commit / push
GitHub main → Cloudflare Pages 빌드 → 정적 HTML·검색·RSS
```

**배포 순서는 [docs/deployment.md](docs/deployment.md)를 보세요.** 키와 변수를 등록하고 수동 실행을 확인한 다음 `COLLECTION_ENABLED=true`로 예약을 켭니다. Codex 클라우드는 개발과 수동 테스트용이며 운영 스케줄러가 아닙니다. 상시 VM에서 직접 돌릴 경우 [Oracle + cron 대안](docs/deployment-oracle.md)도 사용할 수 있습니다. 두 실행기를 동시에 켜지 않습니다.

## 구현된 기능

- 고정된 24시간의 자료를 사건별로 묶는 AI 일일 브리핑, 사실별 근거 링크와 별도 AI 해석
- 주요 이슈에 사용한 기사별 AI 요약을 같은 요청에서 생성·검증해 아카이브에 반영
- 날짜별 히스토리(`/briefings/`), 월·키워드 필터, 하루 한 편의 고유 주소(`/briefings/YYYY-MM-DD/`)
- 당시 요약·출처 보존, 같은 날짜 중복 생성 방지, 실패 시 이전 브리핑 유지
- 전날 06:00 이상~당일 06:00 미만의 기사 대상. 발행 예정 06:30과 실제 생성 시각을 구분

- 상단의 오늘 날짜(KST), 최신 브리핑과 최근 7일 브리핑 카드로 구성한 홈
- 뉴스 아카이브의 카테고리 탭, 키워드·발행일 범위·출처·국내외·AI 요약·북마크 복합 필터
- 카드/리스트 보기 전환, 브라우저 북마크와 보기 방식 저장, URL로 검색 조건 유지
- 한국어·영문 정적 전체 검색(Pagefind), RSS, 사이트맵, canonical·OG·구조화 메타데이터
- 검증한 14개 출처: RSS/Atom 12개, Hacker News 공식 API, Anthropic 공식 HTML
- 한국 시간 보정, 미래/오래된 기사 제외, 혼합 매체 AI 필터, URL/동일 제목 중복 제거
- OpenRouter 한국어 요약·의미 설명, 출력·근거 구절 검증, 호출 수 제한·실패 후 재요청 대기
- 일부 소스 실패 시 이전 기사 보존, 절반 미만의 소스만 성공하면 배치 중단, 원자적 JSON 쓰기
- Actions 중복 실행 방지와 cron 잠금, 데이터 변경 시 단일 커밋. VM에서는 미전송 커밋 재전송, Actions에서는 실패 배치 아티팩트 보관
- 운영자의 기사 숨김·정정 메모 설정

초기 데이터는 2026-10-16 실제 수집 결과입니다. 데모 기사를 만들지 않았습니다. OpenRouter의 `z-ai/glm-5.3-flash`로 실제 한국어 요약 1건을 생성하고 원문 대조와 화면 검증을 수행했습니다. 키나 모델이 없거나, 본문이 부족하거나, 요약 검증에 실패하면 원문 링크로 제공합니다.

## 개발

Node.js 24와 Python 3.10 이상이 필요합니다. **운영 수집기에는 Python만 필요**합니다.

```bash
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm run dev
```

수동 수집은 예약 작업을 설치하지 않습니다.

```bash
# API 비용 없이 현재 소스 수집. 기본 최근 3일, 소스당 최대 12개.
.venv/bin/python -m collector --no-llm
# 첫 수집/복구 시 최대 14일 확인
.venv/bin/python -m collector --no-llm --days 14
# 원본은 보존하고 수집만 검사. LLM도 호출하지 않음.
.venv/bin/python -m collector --dry-run
# .env에 OpenRouter 설정이 있을 때 일일 브리핑 생성 (같은 날짜는 건너뜀)
.venv/bin/python -m collector brief
# 실패한 특정 날짜만 한 차례 재시도 허용 (판별+생성 최대 2회 요청, 기존 발행본 보존)
.venv/bin/python -m collector brief --briefing-date 2026-10-20 --retry-failed-briefing
# 영어 기사만 한국어 제목·요약 생성 (예약 수집도 이 옵션 사용)
.venv/bin/python -m collector collect --summarize-english
# 언어와 무관하게 개별 기사 요약을 생성하는 수동 옵션
.venv/bin/python -m collector collect --summarize-articles
```

수동 수집은 `data/catalog.json`과 `data/news/`·`data/briefings/`의 월별 JSON을 변경할 수 있지만 Git 커밋·push를 하지 않습니다. **운영용 `ops/run-collector.sh`만 커밋·push합니다.** 개발 머신에 운영 cron을 설치하지 마세요.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m collector validate
npm run check
npm run build
npm run preview

# 브라우저 테스트(빌드 후). 개발 머신에서만 설치.
npx playwright install chromium
npm run test:browser
```

이미 Chromium이 있으면 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm run test:browser`. 검색 인덱스는 `npm run build`에서 생성하므로 검색 검증은 dev 대신 preview를 사용합니다. 한국어 검색은 지원하지만 어간 분석은 지원하지 않습니다. 아카이브·히스토리·통합 검색 모두 Pagefind 색인으로 전체 보관 기간을 검색합니다. 목록은 기사 18개, 브리핑 12개씩 페이지를 이동하며 읽습니다.

권한이 제한된 Codex 환경에서는 `NPM_CONFIG_CACHE=/tmp/ai-brief-npm-cache`, `ASTRO_TELEMETRY_DISABLED=1` 환경 변수를 사용합니다. 테스트용 서버는 운영 서버가 아닙니다.

## 설정과 데이터

| 파일 | 역할 |
| --- | --- |
| `config/sources.json` | 실제 수집하는 출처·어댑터·필터·본문/요약 정책 |
| `config/editorial.json` | `hidden_ids`로 숨김, `corrections`의 기사 ID에 정정 메모 |
| `data/news/YYYY-MM.json` | 공개하는 메타데이터·짧은 발췌·요약. 전문과 키 없음 |
| `data/briefings/YYYY-MM.json` | 날짜별 일일 브리핑 스냅샷과 근거 링크. 전문·근거 원문 인용은 저장하지 않음 |
| `data/catalog.json` | 월별 파일 목록·개수·무결성 해시·수집 구간 |
| `data/sources.json` | 현재·과거 기사의 출처 메타데이터 |
| `.runtime/archive-index-*.sqlite3` | 월별 원본에서 재생성하는 로컬 조회 색인. Git 제외 |
| `.runtime/last-run.json` | 마지막 수집 결과. Git 제외, Actions 아티팩트 또는 VM에 보존 |
| `.runtime/summary-attempts.json` | 유료 요청 대기 상태. Actions 캐시 또는 VM에 보존. 캐시는 삭제될 수 있음 |
| `.github/workflows/collect.yml` | 6시간 수집·매일 06:30 브리핑 및 수동 실행, 기본 `GITHUB_TOKEN`으로 게시 |
| `.env` | Oracle 또는 개발 환경의 비밀 설정. Git 제외 |
| `docs/source-research.json` | 검토한 49개 후보의 연구 기록. 운영 설정과 별개 |

새 소스는 설정의 `enabled`로 제어합니다. 비활성화는 새 수집을 중지하며 기존 기사를 삭제하지 않습니다. `hidden_ids`와 `corrections`는 다음 수집에서 반영됩니다. 긴급 정정은 운영 스크립트를 수동 실행하세요. 기사 ID는 상세 URL에 있습니다.

```json
{
  "hidden_ids": ["실제 20자리 기사 ID"],
  "corrections": { "다른 실제 기사 ID": "2026-10-16 정정: 변경 내용과 근거" }
}
```

## 현재 범위

- 기사 데이터는 동일 URL과 72시간 내 충분히 긴 동일 제목만 합칩니다. 브리핑에서는 AI가 관련 사건을 묶지만 장기적으로 같은 이슈를 추적하는 고정 ID는 아직 없습니다.
- 요약은 처음 성공한 결과를 재사용합니다. 원문 수정 추적·자동 재요약, 로그인, 이메일 발송, 개인화, 주간 AI 편집 보고서는 후속 범위입니다.
- 본문은 요약 요청에만 사용하며 공개 JSON에는 저장하지 않습니다. TechCrunch와 커뮤니티 소스는 현재 링크 전용입니다. 본문 요청은 허용 호스트·robots·유료 콘텐츠 표시를 확인합니다. 공개 피드가 재배포 권리를 보장하지는 않습니다.
- HN은 최근 제출 60개, 다른 소스는 피드가 제공하는 항목 안에서 선별합니다. 6시간 사이의 모든 기사를 보장하지 않습니다.
- `MAX_SUMMARIES_PER_RUN`의 기본값은 1입니다. 일일 브리핑은 값이 1 이상이면 중복 판별 1회와 생성 1회, 최대 2회 요청합니다. 비교할 최근 발행 이슈가 없으면 판별 요청은 생략합니다. 0이면 모든 LLM 호출을 중지합니다. 예약 수집은 별도 `ENGLISH_SUMMARIES_PER_RUN` 한도(기본 10, 최대 30)로 영어 기사의 한국어 제목·요약을 생성합니다. 이 값만 0으로 설정하면 영어 기사 자동 요약만 중지합니다. 수동 `--summarize-articles` 옵션의 기사 요약에도 이 상한을 적용합니다. 브리핑 입력은 최대 16건, 본문 발췌는 건당 최대 2,400자이며 기본 출력 한도는 18,432토큰(`BRIEFING_MAX_TOKENS`)입니다. 모든 수집 기사가 브리핑에 포함되는 것은 아닙니다.
- 브리핑은 정해진 06:00~06:00 범위만 사용합니다. 06:30 전에 수동 실행하면 직전 발행일을 대상으로 합니다. 아침 수집이 누락되면 먼저 수집을 복구합니다. 영어 기사 요약은 기본 3일 범위에서 미처리 기사를 수집된 순서대로 검토하며, 본문을 확보할 수 있어야 합니다. 실패한 기사 요청은 24시간 대기합니다. 언어 판별은 본문의 문자 비율·영어 단어를 이용한 휴리스틱입니다. 처음 수집 때 `--no-llm`으로 넣은 오래된 기사까지 소급 요약하려면 `--days 14` 같은 범위를 지정해야 합니다.
- 브리핑은 실제 생성 시점부터 누적하며 과거 기록을 임의로 만들지 않습니다. 같은 발행일은 자료나 모델이 바뀌어도 덮어쓰지 않습니다. GitHub 예약과 Pages 빌드 지연으로 06:30보다 늦게 게시될 수 있습니다.
- 기사는 원문 발행일의 KST 월, 브리핑은 발행일의 월로 나누어 보관합니다. 과거 기록을 자동 삭제하지 않으며 기존 주소와 북마크 ID를 유지합니다. 5,000개 전체 저장 제한은 제거했습니다. 정적 전체 빌드 시간·검색 색인·Pages 파일 수는 여전히 누적 데이터에 따라 증가합니다. [아카이브 구조와 운영 기준](docs/archive-storage.md)을 참고하세요.
- 외부 접속·실제 유료 LLM·Actions 예약 실행·Pages 배포는 서로 다른 검증 단계입니다. [검증 기록](docs/verification.md)에 실행 여부를 구분했습니다.
