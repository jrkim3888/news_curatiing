# GitHub Actions 수집 + Cloudflare Pages 배포

## 실행 위치

```text
Codex: 개발·수동 테스트
GitHub Actions: KST 00/06/12/18시 수집, 매일 06:30 일일 브리핑
각 작업: JSON 검증 → 변경 시 1회 commit/push
GitHub main: 코드·공개 뉴스 데이터 보관
Cloudflare Pages: main 변경 시 정적 HTML·Pagefind 검색·RSS 빌드 및 배포
```

Codex 클라우드는 상시 운영 서버가 아닙니다. 환경 저장은 파일과 설정을 보존하는 기능이며, 실행 중인 cron/프로세스의 계속 실행을 보장하지 않습니다. Actions가 매번 새 실행기를 준비하므로 개발 환경을 켜 둘 필요가 없습니다. Oracle 없이 운영할 수 있고, VM을 직접 관리하려면 [Oracle + cron 대안](deployment-oracle.md)을 사용하세요. 두 수집기를 동시에 켜지 않습니다.

## 1. GitHub에 코드 게시

워크플로 파일은 `.github/workflows/collect.yml`입니다. `main`을 기본 브랜치로 사용합니다. 새 빈 저장소에 아직 코드가 없다면 개발 폴더에서 다음과 같이 게시합니다. 기존 main이 있다면 일반 리뷰·병합 절차를 사용하며 강제 push하지 않습니다.

```bash
git status
git add README.md package.json package-lock.json astro.config.mjs tsconfig.json \
  playwright.config.ts requirements.txt .node-version .gitignore .env.example \
  .prettierrc.json .prettierignore src public collector config data docs ops tests .github
git commit -m "feat: launch AI news curation service"
git push -u origin HEAD:main
```

`.env`, `.runtime`, `.venv`, `node_modules`, `dist`는 Git에서 제외합니다. 예약은 `COLLECTION_ENABLED=true`를 등록하기 전까지 실행되지 않습니다.

## 2. OpenRouter 키와 변수 등록

[저장소 설정 → Secrets and variables → Actions](https://github.com/jrkim3888/news_curatiing/settings/secrets/actions)에서 등록합니다. Codex의 환경 설정은 GitHub Actions로 자동 복사되지 않습니다. 실제 키는 GitHub의 Secret 입력란에만 넣으세요.

| 위치 | 이름 | 값 |
| --- | --- | --- |
| Repository secret | `OPENROUTER_API_KEY` | 서비스 전용 OpenRouter API 키 |
| Repository variable | `OPENROUTER_MODEL` | 사용할 모델 ID. 현재 운영 값 `deepseek/deepseek-v4.1-flash` |
| Repository variable | `MAX_SUMMARIES_PER_RUN` | `1` 권장. 일일 브리핑은 최대 1회 요청, `0`은 생성 중지 |
| Repository variable | `ENGLISH_SUMMARIES_PER_RUN` | 수집당 영어 기사 한국어 요약 수. 기본 `10`, 최대 `30`, `0`은 영어 자동 요약 중지 |
| Repository variable | `BRIEFING_MAX_TOKENS` | 기본 `18432`. 브리핑과 주요 기사별 요약을 합친 출력 한도 |
| Repository variable | `SITE_URL` | 생성한 Pages 주소 또는 연결한 도메인 |
| Repository variable | `COLLECTION_ENABLED` | 처음에는 `false`, 수동 검증 후 `true` |

**개인 GitHub 토큰은 필요 없습니다.** 워크플로가 제공받는 `GITHUB_TOKEN`에 `contents: write`를 요청해 이 저장소에만 결과를 게시합니다. 조직 정책이나 main 보호 규칙이 직접 push를 막으면 해당 정책에 맞는 게시 경로를 구성해야 합니다. 무조건 광범위한 개인 토큰으로 우회하지 않습니다.

OpenRouter 키에는 별도의 credit limit을 설정하세요. 기본 한도에서 수집 4회 × 영어 기사 최대 10건 + 일일 브리핑 1회로, 하루 최대 41회 유료 요청이 가능합니다. 실제 요청은 확보된 영어 본문·미요약 기사 수에 따라 줄어들며, 이미 요약한 기사와 발행한 브리핑은 재사용합니다. 영어 요약 한도는 `ENGLISH_SUMMARIES_PER_RUN`으로 조정합니다. `MAX_SUMMARIES_PER_RUN=0`은 요약 호출을 중지하고 링크 수집을 계속합니다. 호출 수 한도는 달러 금액의 상한이 아닙니다.

종합 브리핑은 최대 16건·건당 본문 발췌 2,400자와 기본 출력 **18,432토큰** 한도를 사용합니다. `BRIEFING_MAX_TOKENS`로 1,024~65,536 범위에서 설정할 수 있으며 실제 사용 모델의 지원 한도 내에서 지정합니다. 모델에 따라 추론 토큰도 응답 예산에 포함됩니다. 한도를 높인 만큼 항상 소비하는 것은 아니지만 요청당 최대 비용은 늘어날 수 있습니다.

`deepseek/deepseek-v4.1-flash`는 OpenRouter 모델 메타데이터상 추론이 선택 사항이고 기본 강도가 `high`입니다. 실제 실행에서 18,432토큰 중 18,404토큰을 추론에 사용해 응답이 잘렸으므로, 이 모델에는 지원되는 `reasoning.effort=none`을 명시합니다. 추론이 필수인 GLM 5.3 Flash는 기존 `low`를 유지합니다. 모델별 옵션은 `collector/llm.py`에 있으며 새 모델로 바꾸면 기본 추론 설정과 지원 옵션도 확인하세요.

브리핑의 주요 이슈에 사용된 모든 기사에 대해 **같은 요청에서 기사별 AI 요약도 생성**합니다. 각 기사의 허용된 원문 발췌에 근거 구절이 있는지 별도로 검사하며, 다른 기사에서 가져온 근거·누락·중복은 거부합니다. 성공하면 브리핑과 월별 기사 파일을 함께 저장하고 기사 상태를 `summarized`로 바꾸므로 아카이브의 ‘AI 요약’ 필터·상세·검색에 반영됩니다. 기존 기사 요약은 보존하며, 브리핑에 선정되지 않은 영어 기사도 별도의 6시간 수집 작업에서 한국어 요약 대상이 됩니다. 이미 발행한 과거 브리핑의 기사를 소급 생성하지는 않습니다.

이 방식은 기사마다 추가 호출하지 않지만 출력 토큰 사용량은 늘어납니다. 별도의 수동 개별 기사 요약은 GLM 4,096토큰, 다른 모델 기본 1,600토큰입니다. 브리핑은 JSON Schema 구조화 출력을 지원하는 제공자로 라우팅합니다. 이슈 최대 5개·문단 최대 3개·각 문단의 완결된 문장 배열 2~3개·기사 ID 목록과 필수 필드를 요청에 명시하고, 원문 근거·선정 기사 요약 누락·문장 형식은 수신 후 별도로 검증합니다. 실패한 응답은 공개 요약에 반영하지 않습니다. 브리핑 요청은 최대 180초의 네트워크 대기를 허용하고 유료 POST를 자동 재전송하지 않습니다.

### 수집 시 영어 기사 한국어 요약

운영 래퍼는 `collect --summarize-english`로 실행합니다. 요약이 허용된 출처에서 확보한 영어 본문을 한국어 제목·2~3문장 요약·의미 설명으로 정리하고, 기사 ID·원문 제목·원문 링크는 보존합니다. 기존 아카이브와 ‘AI 요약’ 필터, 상세 페이지, 검색에 바로 반영됩니다. 제목이나 해외 출처라는 이유만으로 요약하지 않습니다.

기본 최근 3일의 미요약 기사를 수집된 순서대로 검토합니다. 한도 초과분은 다음 수집에서 다시 확인하며, 피드에서 사라졌더라도 허용된 원문을 다시 확보할 수 있으면 처리합니다. 본문 요청은 실행당 최대 24건입니다. 본문 부족·접근 불가·링크 전용 출처·요약 검증 실패는 원문 링크로 남습니다. 현재 TechCrunch·HN·GeekNews의 링크 전용 정책은 유지합니다. 일일 브리핑의 수집 복구 단계는 별도 기사 요약 없이 실행합니다.

`last-run.json`의 `article_summaries`에는 모드·한도·생성 수·미검토 후보 수·건너뛴 사유가 남습니다. `remaining_candidates`는 아직 언어·본문을 확인하지 않은 링크 후보 수이며 모두 영어 요약 가능 기사라는 의미는 아닙니다. `no_llm`, `dry_run`, 키/모델 누락, 전체 한도 0은 유료 호출을 막습니다. 영어 기사 요약 실패도 기사별 24시간 재요청 대기를 적용합니다.

## 3. Cloudflare Pages 연결

Cloudflare 대시보드의 **Workers & Pages → Create application → Pages → Connect to Git**에서 GitHub를 연결합니다. 화면 명칭이 달라졌다면 정적 Pages의 Git 저장소 연결을 선택하세요. 기존 블로그와 별도의 Pages 프로젝트를 만들고 `jrkim3888/news_curatiing`을 선택합니다.

| 항목 | 값 |
| --- | --- |
| Production branch | `main` |
| Framework preset | Astro 또는 None |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Root directory | 저장소 루트, 비워둠 |
| Environment variable | `NODE_VERSION=24` |
| Environment variable | `ASTRO_TELEMETRY_DISABLED=1` |
| Environment variable | `SITE_URL=https://실제프로젝트.pages.dev` |

생성 후 실제 `*.pages.dev` 주소를 확인해 Pages와 GitHub 변수 양쪽의 `SITE_URL`을 맞추고 다시 배포합니다. 개발 기본값 `https://ai-brief.pages.dev`는 사용자가 확보한 주소가 아닙니다. 도메인을 붙일 때도 이 값을 변경하세요.

**Pages에는 OpenRouter 키를 넣지 않습니다.** 빌드는 저장된 JSON으로 정적 파일만 생성합니다. 유료 Workers, Pages Functions, Cloudflare API 토큰, Deploy hook은 필요 없습니다. Pages Git 연결의 자동 생산 배포를 켜 두세요.

첫 배포에서 홈, 기사 상세, `/search/`, `/rss.xml`, `/sitemap.xml`을 확인합니다. 수집기가 생성한 data 커밋으로 Pages 배포가 시작되는지도 확인해야 합니다. `GITHUB_TOKEN`의 push가 다른 GitHub Actions 워크플로를 다시 실행하지 않는 제한과, 별도 Cloudflare Git 연동은 구분합니다. Pages가 갱신되지 않으면 GitHub 앱의 저장소 접근 권한, 생산 브랜치와 Pages 자동 배포 설정을 확인하세요.

## 4. 수동 실행 후 수집·브리핑 예약 켜기

1. GitHub 저장소 **Actions → Collect AI news → Run workflow**에서 `main`을 선택합니다. `mode=collect`, `no_llm`을 체크한 기본값으로 실행합니다. 실제 수집·검증·변경 시 커밋까지 수행하되 OpenRouter 호출은 없습니다.
2. 작업 성공과 data 커밋, 그 커밋의 Pages 배포를 확인합니다. 변경이 없으면 커밋이나 배포가 생기지 않는 것이 정상입니다.
3. 영어 요약을 확인하려면 `mode=collect`에서 `no_llm`을 해제합니다. 키·모델·`MAX_SUMMARIES_PER_RUN=1` 등록 후 `mode=brief`를 선택하고 `no_llm`을 해제해 일일 브리핑을 수동 실행합니다. 로그의 요청 수와 아티팩트의 `last-run.json`에서 요약 실패 여부를 확인합니다. 이미 발행한 날짜라면 유료 요청 없이 건너뜁니다.
4. `COLLECTION_ENABLED=true`로 바꿉니다. 수집은 UTC `0 3,9,15,21 * * *`(KST **00:00 / 06:00 / 12:00 / 18:00**), 브리핑은 UTC `30 21 * * *`(KST **매일 06:30**)입니다.

브리핑은 **전날 06:00 이상~당일 06:00 미만**의 기사를 다룹니다. 06:00 수집 성공 기록이 없으면 브리핑 작업에서 먼저 수집을 복구합니다. 두 작업은 같은 동시 실행 그룹과 잠금을 사용합니다. 06:30 전 수동 발행은 직전 발행일을 대상으로 하며, 여러 날 누락된 과거 기록은 자동으로 채우지 않습니다.

Actions 예약과 Pages 빌드 때문에 실제 게시가 06:30보다 늦을 수 있습니다. Actions의 예약은 정확한 시각을 보장하지 않으며 부하에 따라 지연되거나 누락될 수 있습니다. 기본 브랜치의 워크플로만 예약됩니다. 공개 저장소는 60일간 활동이 없으면 예약이 비활성화될 수 있으니 Actions 실행 기록을 확인하세요. `COLLECTION_ENABLED=false`로 바꾸면 예약 수집은 중지되고 마지막 사이트 배포는 유지됩니다. 수동 실행은 계속 가능합니다.

## 브리핑 히스토리 확인

- 수동 실행 후 `last-run.json`의 `briefing.result`가 `created`인지 확인합니다. `already_published`는 같은 날짜의 중복 생성을 건너뛴 것이며, `insufficient_sources`는 고정된 24시간 범위에 요약 가능한 자료가 2건 미만인 경우입니다. `cooldown`은 그날 이미 유료 요청을 시도한 상태입니다.
- Pages에서 `/briefings/` → 날짜를 선택해 당시 요약과 관련 원문을 확인합니다. 날짜당 한 편이며 `/briefings/YYYY-MM-DD/`에서 24시간 집계 범위, 발행 예정·실제 생성 시각을 표시합니다. 첫 성공 생성 전에는 빈 기록 안내가 정상입니다.
- 날짜별 기록은 Git에 보존됩니다. 캐시를 지워도 기록은 유지됩니다. 단, 실패한 요청의 대기 시간은 캐시 유실 시 보장되지 않습니다.
- 종합 브리핑과 주요 기사별 요약의 기본 출력 한도는 18,432토큰입니다. 기존 단일 기사 요약 비용을 그대로 적용하지 마세요.

## 실패한 날짜의 브리핑 재발행

먼저 Actions의 `[briefing-diagnostics]` 로그 또는 아티팩트의 `.runtime/last-run.json`을 확인합니다. `briefing.reason`·`stage`는 실패 원인과 단계이며, `llm`에는 요청 모델·출력 한도, 제공자가 반환한 모델·요청 ID·종료 사유·입력/응답/추론 토큰·비용이 기록됩니다. 응답에 없는 사용량은 추정하지 않습니다. 키·원문·모델 응답 본문·추론 내용은 이 기록에 포함하지 않습니다.

- `output_token_limit`: 모델이 `finish_reason=length`로 응답을 중단했습니다. 출력 예산과 추론 사용량을 확인합니다.
- `invalid_response_json`: 응답을 기대한 JSON 구조로 읽을 수 없습니다.
- `http_429`, `http_5xx`, `network_failed_or_timed_out`: API 제한·서버·네트워크 문제입니다.
- `validation_failed`, `stage=briefing_validation` / `article_summary_validation`: 브리핑 / 기사별 요약 검증에서 실패했습니다. `validation_error`에는 원문이나 응답을 포함하지 않는 검증 항목 설명이 남습니다. 문장 형태 오류는 `validation_details`에 실패 필드 위치·문장 수·글자 수·종결 여부·줄바꿈/말줄임표 여부를 추가합니다. 원문 응답이나 실패 문장 전문은 저장하지 않습니다.

원인을 수정한 후 **Actions → Collect AI news → Run workflow**에서 아래와 같이 실행합니다.

| 입력 | 예: 실패한 2026-10-20 브리핑 |
| --- | --- |
| Branch | `main` |
| `mode` | `brief` |
| `no_llm` | 체크 해제 |
| `briefing_date` | `2026-10-20` |
| `retry_failed_briefing` | 체크 |

실행당 최대 **1회 추가 유료 요청**을 허용합니다. 해당 날짜의 대기 기록만 무시하며 다른 날짜·기사의 기록은 보존합니다. 이미 발행·비공개 처리한 날짜는 덮어쓰지 않습니다. 기본 예약과 일반 재실행은 여전히 같은 날짜의 추가 요청을 막습니다. 미래 날짜와 발행 예정 시각 이전의 날짜는 거부하며, `no_llm` 또는 호출 한도 `0`이면 재시도도 호출하지 않습니다. 캐시 전체를 지울 필요는 없습니다.

지난 날짜를 지정하면 그 날짜의 06:00 기준 24시간 범위를 유지합니다. 당시의 허용된 원문을 다시 확보할 수 없으면 발행하지 않습니다. 성공한 실행은 `briefing.result=created`, `articles_summarized`에 이번에 요약한 기사 수를 기록하고 data 커밋으로 Pages를 갱신합니다.

## 상태 보존과 복구

월별 저장·검색·이관 및 복구의 상세 절차는 [아카이브 운영 문서](archive-storage.md)를 참고하세요.

- 뉴스와 성공한 기사 요약의 기준 데이터는 **Git의 `data/news/YYYY-MM.json`**, 날짜별 브리핑 기록은 **`data/briefings/YYYY-MM.json`**입니다. `data/catalog.json`·`data/sources.json`과 변경된 월별 파일을 검증한 뒤 한 번 커밋합니다. 실행기가 사라져도 다음 작업이 다시 받으며 이미 성공한 요약을 반복 생성하지 않습니다.
- 브리핑은 날짜별 요청 시도를, 수집 중 개별 기사 요약은 24시간 대기 상태를 `.runtime/summary-attempts.json`에 먼저 기록하고 Actions 캐시로 복원합니다. 캐시는 삭제·만료될 수 있으므로 대기 시간은 캐시가 유지될 때 보장됩니다. 캐시 유실·강제 종료 직후에는 실패했던 요청을 다시 시도할 수 있습니다. 브리핑 생성에 실패한 날은 같은 날짜의 자동 재요청을 막고 이전 발행본을 유지합니다. 운영자가 진단 후 복구해야 하며, 중복 과금 가능성을 확인하지 않고 캐시를 지우지 마세요. 요청 수 상한과 OpenRouter 키 credit limit을 함께 사용하세요.
- `data/catalog.json`의 `collection_slot`은 마지막 성공 수집 구간입니다. 기사 변경이 없어도 새 6시간 구간이면 기록하고 커밋합니다. 기사 내용의 `updated_at`과 구분합니다.
- 중복 실행은 Actions `concurrency`와 스크립트의 `flock`으로 막습니다. 실행은 50분, 전체 job은 55분으로 제한합니다.
- 수집 실패 시 원래 공개 데이터를 보존합니다. 절반 미만 소스가 성공하면 배치를 실패 처리합니다. 일부 소스/LLM 실패는 링크 또는 이전 기사로 남습니다.
- 실패 진단과 복구를 위해 `collection-실행ID-시도번호` 아티팩트에 공개 `data/catalog.json`·`data/sources.json`·월별 뉴스/브리핑 디렉터리, 안전한 실행 보고서와 요청 시각을 7일 보관합니다. `.env`, 키, 기사 전문은 업로드하지 않습니다. 강제 취소나 runner 장애에서는 저장 단계도 실행되지 않을 수 있습니다.
- **Actions에서 push가 실패하면** 일회성 실행기의 로컬 커밋은 다음 작업으로 이어지지 않습니다. 아티팩트의 catalog·sources·월별 뉴스/브리핑 JSON을 함께 받아 최신 main과 비교·병합하고 `python -m collector validate` 후 커밋하세요. 최신 데이터를 통째로 덮어쓰지 마세요. 다음 실행은 원격의 마지막 성공 데이터를 기준으로 수집하며 전송하지 못한 배치를 자동 복구하지는 않습니다.
- **main이 수집 도중 갱신되면** 충돌 위험이 있는 자동 덮어쓰기를 하지 않고 push가 실패합니다. 위 복구 절차를 따릅니다. Oracle 대안은 고정 디스크에 실패한 커밋을 보관해 다음 실행에서 재전송하는 방식입니다.
- 정정·삭제는 `config/editorial.json` 수정 후 수동 수집으로 반영합니다. Pages 배포가 완료돼야 사용자 화면도 바뀝니다.

## 비용과 현재 검증 범위

현재 저장소는 공개 저장소입니다. 공개 저장소의 표준 GitHub 호스팅 Linux 실행기는 무료 사용 대상입니다. 비공개 저장소, 대형 실행기, 저장 공간 등은 계정 요금제·한도에 따라 달라집니다. 짧은 아티팩트 보관 기간을 사용합니다.

하루 수집 4회와 브리핑 1회가 각각 배포되면 월 약 150~155회입니다. Cloudflare Pages Free의 월 500회 빌드 한도에는 코드 변경·재시도·같은 계정의 다른 프로젝트도 함께 고려해야 합니다. OpenRouter와 도메인 비용은 별도입니다.

로컬의 Python 수집·커밋·실패 복구 테스트와 워크플로 검사는 [검증 기록](verification.md)에 구분합니다. GitHub에서의 실제 예약 실행과 Cloudflare의 실제 배포는 키·프로젝트 설정 후 확인해야 합니다.

공식 안내: [GitHub 예약 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions 요금](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Pages Git 연결](https://developers.cloudflare.com/pages/configuration/git-integration/), [Pages 제한](https://developers.cloudflare.com/pages/platform/limits/).
