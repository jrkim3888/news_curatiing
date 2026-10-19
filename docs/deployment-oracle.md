# 대안: Oracle Python 수집기 + Cloudflare Pages 배포

현재 기본 배포는 [GitHub Actions + Pages](deployment.md)입니다. 아래는 상시 VM을 직접 관리할 때의 대안입니다. 두 수집기를 동시에 활성화하지 마세요. Oracle로 전환하기 전에 GitHub의 `COLLECTION_ENABLED`를 `false`로 바꿉니다.

## Oracle 구성

- **Oracle VM**: Python 가상환경, cron, Git, `flock`, `timeout`만 사용합니다. Docker·Node.js·웹 서버·DB가 필요 없습니다. 수집은 외부로 나가는 HTTPS/SSH 연결만 사용하므로 웹 인바운드 포트를 열 필요가 없습니다.
- **GitHub**: 코드와 공개 데이터 저장. 매 배치에서 데이터가 바뀐 경우에만 한 번 커밋합니다.
- **Cloudflare Pages**: Git 연결을 통해 Node.js로 빌드하고 정적 결과를 배포합니다. Pages Functions·Workers를 사용하지 않습니다.
- **Codex/로컬 개발 환경**: 개발·수동 수집·브라우저 검증용. 여기에는 운영 cron을 설치하지 않습니다.

소규모 단일 VM에는 cron이 가장 간단합니다. 재부팅 직후 누락 실행 보충이 필요해지면 systemd 타이머로 실행기만 교체할 수 있습니다. 이 대안을 선택하면 GitHub Actions 수집 예약은 끄고 코드 CI만 사용합니다.

## 1. 코드를 GitHub의 main에 올리기

빈 저장소에서 시작한 첫 배포라면 개발 작업 폴더에서 변경 내용을 검토한 뒤 다음 순서로 올립니다. 이미 커밋된 작업 브랜치가 있다면 해당 브랜치를 main에 병합하는 일반 절차를 사용하세요.

```bash
git status
git add README.md package.json package-lock.json astro.config.mjs tsconfig.json \
  playwright.config.ts requirements.txt .node-version .gitignore .env.example \
  .prettierrc.json .prettierignore src public collector config data docs ops tests .github
git commit -m "feat: launch AI news curation service"
git branch -M main
git push -u origin main
```

`.env`, `.venv`, `.runtime`, `node_modules`, `dist`는 올리지 않습니다. 저장소 설정에서 main을 기본 브랜치로 지정합니다. 이 문서의 명령은 기존 브랜치 강제 덮어쓰기를 하지 않습니다.

## 2. Cloudflare Pages 연결

Cloudflare 대시보드에서 **Pages → Git 저장소 연결**을 선택하고 `jrkim3888/news_curatiing`을 연결합니다. 프로젝트 이름은 원하는 값으로 정하세요. 기존 블로그 Pages 프로젝트와 별도로 생성합니다.

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

첫 프로젝트 생성 과정에서 URL을 아직 모르면 생성된 `*.pages.dev` URL을 확인한 뒤 `SITE_URL`을 바꾸고 다시 배포하세요. 기본 개발용 주소 `https://ai-brief.pages.dev`는 예약된 사용자 주소가 아니므로 그대로 운영하지 않습니다. 도메인을 연결하면 `SITE_URL`도 그 도메인으로 바꿉니다. Preview에서도 실제 운영 URL을 canonical로 쓸지 정책을 정합니다.

**OpenRouter 키는 Pages에 넣지 않습니다.** Pages는 이미 만들어진 JSON을 읽고 HTML과 검색 인덱스를 만듭니다. 빌드 중 수집이나 LLM 요청을 하지 않습니다. Deploy hook이나 Cloudflare API 토큰도 필요 없습니다.

첫 배포에서 홈, 기사 상세, `/search/`, `/rss.xml`, `/sitemap.xml`을 확인하세요. 빌드 실패 시 기존 성공 배포는 유지됩니다. 실패한 배포의 로그를 확인하고 코드/데이터를 수정해 다시 배포합니다.

## 3. Oracle VM 준비

아래는 Ubuntu 22.04/24.04 기준 예시입니다. Oracle Linux를 사용한다면 `apt`와 `cron` 서비스 이름을 해당 배포판의 패키지 관리자·`crond`에 맞게 바꾸세요. Python 3.10 이상이 필요합니다. ARM·x86 모두 수집 코드가 동일합니다.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git cron util-linux ca-certificates openssh-client logrotate
sudo systemctl enable --now cron
sudo adduser --disabled-password --gecos "" news
sudo mkdir -p /opt/ai-brief
sudo chown news:news /opt/ai-brief
sudo -iu news
```

이미 운영 전용 사용자가 있으면 재사용하고 아래 경로·사용자 이름을 맞춥니다. root의 cron에서 돌릴 필요가 없습니다.

### 이 저장소 전용 Git 인증

`news` 사용자로 저장소 전용 SSH deploy key를 만듭니다. 공개키만 GitHub 저장소 **Settings → Deploy keys**에 등록하고 **Allow write access**를 선택합니다. 개인키는 Oracle에만 둡니다. 무인 실행용 키는 이 저장소에만 권한이 있고 OS 파일 권한으로 보호해야 합니다.

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/ai_brief_deploy -C ai-brief-collector
```

패스프레이즈를 설정한다면 cron에서 사용 가능한 SSH agent를 별도로 운영해야 합니다. 간단한 무인 실행은 이 저장소 전용 키를 패스프레이즈 없이 생성하고 개인키 권한을 `600`으로 제한합니다. 키 내용을 채팅이나 Git에 붙이지 마세요.

`~/.ssh/config`에 아래 항목을 추가합니다.

```sshconfig
Host github-ai-brief
    HostName github.com
    User git
    IdentityFile ~/.ssh/ai_brief_deploy
    IdentitiesOnly yes
```

```bash
chmod 600 ~/.ssh/config ~/.ssh/ai_brief_deploy
ssh -T git@github-ai-brief
```

최초 접속에서는 GitHub 공식 [SSH 키 지문](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)과 서버 지문을 비교한 뒤 등록합니다. 정상 인증 메시지와 함께 종료 코드 1이 나올 수 있습니다. 호스트 키 검사를 끄지 않습니다.

```bash
git clone --branch main git@github-ai-brief:jrkim3888/news_curatiing.git /opt/ai-brief
cd /opt/ai-brief
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p .runtime
chmod 700 .runtime
git push --dry-run origin HEAD:main
```

GitHub 브랜치 보호가 deploy key의 직접 push를 막는다면 저장소 정책에 맞는 자동화 계정/GitHub App을 사용하거나 데이터 전용 브랜치를 Pages 생산 브랜치로 지정하는 구성이 필요합니다. 강제 push로 우회하지 않습니다. SSH egress 22번이 막혀 있다면 GitHub의 공식 SSH-over-HTTPS 설정을 사용하거나 HTTPS 인증을 별도로 구성하세요.

## 4. OpenRouter 설정

```bash
cd /opt/ai-brief
cp .env.example .env
chmod 600 .env
nano .env
```

`.env`에 다음 값을 입력합니다. 파일은 Python이 읽으며 shell로 실행하지 않습니다.

```dotenv
OPENROUTER_API_KEY=본인_OpenRouter_키
OPENROUTER_MODEL=선택한_정확한_모델_ID
MAX_SUMMARIES_PER_RUN=1
SITE_URL=https://실제프로젝트.pages.dev
```

[OpenRouter 모델 목록](https://openrouter.ai/models)에서 사용 가능한 모델 ID, 가격, JSON mode 지원을 확인합니다. 무료 모델은 속도 제한·가용성이 달라질 수 있습니다. 이 구현은 Chat Completions와 `response_format: json_object`를 사용합니다.

현재 실제 검증 모델은 **`z-ai/glm-5.3-flash`**입니다. 해당 엔드포인트는 추론을 끌 수 없어 낮은 추론 강도(`reasoning.effort=low`)를 사용하며, 일일 브리핑은 최대 6,144토큰, 개별 기사 요약은 4,096토큰입니다. 1,600토큰에서는 추론이 예산을 소진해 빈 응답이 발생했고, 추론 비활성화는 HTTP 400으로 거절됐습니다. 모델별 옵션은 `collector/llm.py`의 `MODEL_OPTIONS`에 있습니다. JSON 등 요청 옵션을 지원하는 제공자로만 라우팅합니다.

**OpenRouter에서 이 서비스 전용 API 키의 credit limit을 설정하세요.** 일반 수집은 LLM을 호출하지 않고, 일일 브리핑은 하루 최대 1회 요청합니다. 브리핑 입력은 최대 16건·건당 2,400자, 출력은 최대 6,144토큰입니다. 수동 `--summarize-articles` 실행은 별도 비용을 만들 수 있습니다. 호출 수 한도는 달러 금액의 상한이 아니며, 모델별 토큰 단가가 다릅니다. POST timeout은 이미 과금됐을 수 있어 자동 재전송하지 않고, 브리핑은 같은 날짜 재요청을 막습니다. 수동 개별 기사 요약의 대기 시간은 24시간입니다.

`MAX_SUMMARIES_PER_RUN=1`을 유지하면 됩니다. 일일 브리핑은 값을 높여도 한 번만 요청합니다. 키/모델이 비어 있으면 수집·배포는 정상 진행되지만 링크 전용입니다. `.runtime/summary-attempts.json`은 재시도 제어를 위해 보존합니다.

## 5. Oracle에서 수동으로 끝까지 확인

```bash
cd /opt/ai-brief
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m collector --dry-run
NEWS_MODE=collect bash ops/run-collector.sh
NEWS_MODE=brief bash ops/run-collector.sh
tail -n 60 .runtime/last-run.json
git log -1 --oneline
```

`--dry-run`은 수집·검증만 하며 LLM과 공개 데이터 쓰기를 하지 않습니다. `run-collector.sh`는 실제 요약 요청과 데이터 커밋·push를 할 수 있습니다. 실행 전 Git 작업 폴더가 깨끗해야 합니다. 별도로 `.venv/bin/python -m collector`를 실행해 미커밋 데이터가 생겼다면 먼저 검토·커밋하거나 해당 변경을 명시적으로 처리하세요. 운영 래퍼는 사용자 변경을 덮어쓰지 않습니다.

그 다음 GitHub에서 data 커밋이 생겼는지, Pages에서 해당 커밋으로 자동 배포됐는지, 기사 요약·한국어 검색이 보이는지 확인합니다. 데이터가 같으면 커밋이 생기지 않는 것이 정상입니다. 이 end-to-end 확인은 실제 Oracle/GitHub/Pages 권한이 있어야 완료할 수 있습니다.

## 6. cron 등록

VM 시간대를 확인합니다.

```bash
timedatectl
crontab -e
```

`news` 사용자의 crontab에 [ops/collector.crontab.example](../ops/collector.crontab.example) 내용을 추가합니다. 예시는 **UTC 서버 기준**입니다. 수집은 `0 3,9,15,21 * * *`(KST 00/06/12/18시), 브리핑은 `30 21 * * *`(KST 매일 06:30)입니다. 한국 시간 서버에서는 각각 `0 */6 * * *`, `30 6 * * *`를 사용합니다. 브리핑 범위는 전날 06:00 이상~당일 06:00 미만으로 고정합니다. 06:00 수집이 누락되면 브리핑 작업이 먼저 수집을 복구합니다. 다른 기존 cron 항목을 덮어쓰지 마세요.

`flock`은 동시에 두 작업이 실행되는 것을 막습니다. `timeout`은 배치를 50분으로 제한합니다. cron은 VM이 꺼져 있거나 정지된 동안 실행하지 않으며 놓친 실행을 자동 보충하지 않습니다. 재시작 후 수동 실행하거나 다음 예약을 기다립니다. VM의 실제 Free Tier 자원 상태와 가용성은 별도 관리가 필요합니다.

```bash
crontab -l
tail -n 100 /opt/ai-brief/.runtime/cron.log
```

[ops/logrotate.example](../ops/logrotate.example)을 `/etc/logrotate.d/ai-brief`에 설치하면 주 단위 회전·8주 보관합니다. 사용자·경로를 먼저 맞추고 `sudo logrotate --debug /etc/logrotate.d/ai-brief`로 검사합니다. 로그 경로와 `.runtime`은 운영 사용자에게 쓰기 권한이 있어야 합니다.

## 운영과 복구

- **수집 실패**: `last-run.json`과 cron 로그 확인. 전체 소스의 절반 미만이 성공하면 공개 데이터를 보존하고 실패로 종료합니다. 일부 실패는 이전 기사를 유지하며 성공한 소스만 갱신합니다. 본문 수집/LLM 실패는 해당 기사를 링크로 남깁니다.
- **push 실패**: 로컬 커밋을 보존합니다. 다음 실행에서 원격이 그대로라면 동일 커밋을 push합니다. 수집 모드에서는 종료하며, 브리핑 모드에서는 이어서 그날 발행 여부를 확인합니다. 원격과 서로 다른 새 커밋이 생겼다면 자동 병합을 중단하므로 운영자가 변경을 검토해 병합해야 합니다.
- **코드 갱신**: 개발 브랜치를 main에 반영하면 다음 실행이 `--ff-only`로 받아옵니다. requirements.txt가 바뀌면 venv 의존성을 갱신합니다. 사전 CI를 통과한 코드만 main에 넣으세요.
- **강제 종료/정전 후 dirty 상태**: 먼저 `.venv/bin/python -m collector recover`로 중단된 월별 저장을 복구합니다. 래퍼도 다음 실행 전에 이 복구를 시도합니다. 이후 `git status --short`와 `git diff -- data`를 확인합니다. 정상 배치라면 전체 검증 후 관련 월 파일·catalog·sources를 함께 커밋합니다. 알 수 없는 변경이나 수동 편집은 자동 덮어쓰지 않습니다. [월별 아카이브 복구](archive-storage.md)를 참고하세요.
- **요약 비용 급증**: OpenRouter 키 한도와 모델 가격을 먼저 확인합니다. `MAX_SUMMARIES_PER_RUN=0`이면 LLM 요청을 멈추고 링크 수집을 계속합니다.
- **수집 일시 중지**: 해당 cron 줄을 주석 처리합니다. 사이트는 마지막 정적 배포를 계속 제공합니다.
- **정정/삭제**: `config/editorial.json` 수정 → GitHub 반영 → Oracle에서 래퍼 수동 실행 → Pages 배포 확인. 페이지 내용은 다음 배포 전까지 바뀌지 않습니다.
- **VM 복구**: Git 저장소, `.env`의 안전한 별도 백업, deploy key, `.runtime/summary-attempts.json`이 필요합니다. 전문 기사 원본 백업은 없습니다. 정기적으로 로그를 확인하고 수집 실패에 대한 외부 알림은 운영 환경에서 별도로 연결하세요.

## 비용

하루 수집 4회와 일일 브리핑 1회가 모두 배포되면 월 약 150~155회입니다. 코드 변경·재시도·다른 Pages 프로젝트 빌드를 더해 계정의 실제 무료 한도와 비교합니다. 정적 Pages 호스팅에 유료 Workers를 추가할 필요는 없습니다. OpenRouter 호출, 도메인, Oracle 무료 범위 초과 자원은 별도 비용입니다.

공식 문서: [Cloudflare Pages Git 연결](https://developers.cloudflare.com/pages/get-started/git-integration/), [Pages 빌드 설정](https://developers.cloudflare.com/pages/configuration/build-configuration/), [Pages 제한](https://developers.cloudflare.com/pages/platform/limits/).
