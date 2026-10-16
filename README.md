# AI 브리프

국내외 AI 뉴스를 원문과 함께 읽는 한국어 큐레이션 사이트. **GitHub Actions가 6시간마다 Python 수집기를 실행하고, Cloudflare Pages가 정적 프런트를 제공합니다.** 상시 서버, Docker, Workers, DB는 필요하지 않습니다.

```text
GitHub Actions (6시간마다 예약 실행)
  Python 수집 → 필터·중복 제거 → OpenRouter 요약 → JSON 검증
  → 변경된 data/news.json만 한 번 commit / push
GitHub main → Cloudflare Pages 빌드 → 정적 HTML·검색·RSS
```

**배포 순서는 [docs/deployment.md](docs/deployment.md)를 보세요.** 키와 변수를 등록하고 수동 실행을 확인한 다음 `COLLECTION_ENABLED=true`로 예약을 켭니다. Codex 클라우드는 개발과 수동 테스트용이며 운영 스케줄러가 아닙니다. 상시 VM에서 직접 돌릴 경우 [Oracle + cron 대안](docs/deployment-oracle.md)도 사용할 수 있습니다. 두 실행기를 동시에 켜지 않습니다.

## 구현된 기능

- 반응형 홈, 기사 상세, 전체 아카이브, 카테고리·국내외 필터, 검색, 브라우저 북마크
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
# .env에 OpenRouter 설정이 있을 때 실제 요약 포함
.venv/bin/python -m collector
```

수동 수집은 `data/news.json`을 변경할 수 있지만 Git 커밋·push를 하지 않습니다. **운영용 `ops/run-collector.sh`만 커밋·push합니다.** 개발 머신에 운영 cron을 설치하지 마세요.

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

이미 Chromium이 있으면 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm run test:browser`. 검색 인덱스는 `npm run build`에서 생성하므로 검색 검증은 dev 대신 preview를 사용합니다. 한국어 검색은 지원하지만 어간 분석은 지원하지 않습니다. 홈 목록 검색은 부분 문자열 검색입니다.

권한이 제한된 Codex 환경에서는 `NPM_CONFIG_CACHE=/tmp/ai-brief-npm-cache`, `ASTRO_TELEMETRY_DISABLED=1` 환경 변수를 사용합니다. 테스트용 서버는 운영 서버가 아닙니다.

## 설정과 데이터

| 파일 | 역할 |
| --- | --- |
| `config/sources.json` | 실제 수집하는 출처·어댑터·필터·본문/요약 정책 |
| `config/editorial.json` | `hidden_ids`로 숨김, `corrections`의 기사 ID에 정정 메모 |
| `data/news.json` | 공개하는 메타데이터·짧은 발췌·요약. 전문과 키 없음 |
| `.runtime/last-run.json` | 마지막 수집 결과. Git 제외, Actions 아티팩트 또는 VM에 보존 |
| `.runtime/summary-attempts.json` | 유료 요청 대기 상태. Actions 캐시 또는 VM에 보존. 캐시는 삭제될 수 있음 |
| `.github/workflows/collect.yml` | 6시간 예약 및 수동 수집, 기본 `GITHUB_TOKEN`으로 게시 |
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

- 동일 URL과 72시간 내 충분히 긴 동일 제목만 합칩니다. 다국어 의미 기반 사건 군집화는 아직 하지 않습니다.
- 요약은 처음 성공한 결과를 재사용합니다. 원문 수정 추적·자동 재요약, 로그인, 이메일 발송, 개인화, 주간 AI 편집 보고서는 후속 범위입니다.
- 본문은 요약 요청에만 사용하며 공개 JSON에는 저장하지 않습니다. TechCrunch와 커뮤니티 소스는 현재 링크 전용입니다. 본문 요청은 허용 호스트·robots·유료 콘텐츠 표시를 확인합니다. 공개 피드가 재배포 권리를 보장하지는 않습니다.
- HN은 최근 제출 60개, 다른 소스는 피드가 제공하는 항목 안에서 선별합니다. 6시간 사이의 모든 기사를 보장하지 않습니다.
- 기본 3일 범위 안에서 요약을 재시도합니다. 처음 수집 때 `--no-llm`으로 넣은 오래된 기사까지 소급 요약하려면 `--days 14` 같은 범위를 지정해야 합니다.
- 기사 5,000개에서 저장을 중단하는 보호 장치가 있습니다. 장기 운영 전 아카이브 분할과 Pages 파일 수·빌드 시간을 확인하세요. 자동 삭제로 기존 링크를 깨뜨리지 않습니다.
- 외부 접속·실제 유료 LLM·Actions 예약 실행·Pages 배포는 서로 다른 검증 단계입니다. [검증 기록](docs/verification.md)에 실행 여부를 구분했습니다.
