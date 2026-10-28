# Mac + OpenClaw에서 수집·발행 운영

Mac의 OpenClaw Gateway가 예약 시각에 기존 Python 수집기를 실행하고, 변경된 JSON을 GitHub에 push하면 Cloudflare Pages가 사이트를 갱신합니다. Pages 설정과 뉴스·브리핑 저장 위치는 그대로입니다. Mac에 웹 서버나 Docker를 설치할 필요는 없습니다.

OpenClaw의 **command automation**을 사용합니다. 예약마다 에이전트에게 명령 실행을 판단시키지 않고 정해진 실행 파일을 직접 호출하므로, 예약 실행 자체에 추가 모델 요청은 없습니다. 기사 요약과 브리핑은 기존 Python 코드가 `.env`의 OpenRouter 설정으로 생성합니다.

이 절차는 운영 전용 Mac/체크아웃 기준입니다. Mac은 전원과 네트워크에 연결되어 있어야 하며 잠자기 중에는 예정 시각 실행을 보장할 수 없습니다. Gateway를 LaunchAgent로 설치하면 로그인한 사용자 세션에서 실행되므로 재부팅 후 로그인·Gateway 상태를 확인하세요. 잠금 화면과 시스템 잠자기는 다릅니다. GitHub Actions와 Mac 예약을 동시에 켜지 않습니다.

## 1. Mac 준비

[Homebrew](https://brew.sh/)가 설치된 터미널에서 실행합니다.

```bash
brew install python@3.12 git gh flock
```

OpenClaw가 아직 없다면 지원되는 Node.js를 준비한 뒤 설치합니다. 확인한 공식 문서의 요구사항은 Node.js 24.16 이상인 24.x 또는 26.1 이상입니다. 기존 OpenClaw가 설치돼 있으면 먼저 `openclaw --version`과 아래 도움말을 확인하세요.

```bash
brew install node@24
export PATH="$(brew --prefix node@24)/bin:$PATH"
npm install -g openclaw@latest
openclaw
```

초기 설정을 완료하고, 앞의 명령으로 Gateway가 터미널에서 실행 중이면 Ctrl+C로 종료한 후 백그라운드 서비스를 설치합니다.

```bash
openclaw gateway install
openclaw gateway status
openclaw automations create --help
```

도움말에 `--command-argv`, `--command-cwd`, `--command-env`가 있어야 합니다. 아래 예약 명령은 공식 릴리스 **v2026.10.8**의 CLI 문서에서 지원을 확인했습니다. 오래된 버전이면 설치 방식에 맞게 업데이트한 뒤 `openclaw gateway restart`로 적용합니다. `openclaw cron`은 현재 문서에서 `automations`와 같은 명령의 별칭입니다. 명령 작업을 등록할 때는 해당 Mac Gateway의 운영자 계정을 사용합니다.

## 2. 운영 전용 저장소와 Git 인증

OpenClaw Gateway를 실행하는 **같은 macOS 사용자**로 인증합니다. 아래는 HTTPS와 GitHub CLI를 사용하는 방법입니다. GitHub 토큰을 예약 명령이나 채팅에 넣지 않습니다.

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
mkdir -p "$HOME/Services"
git clone --branch main https://github.com/jrkim3888/news_curatiing.git "$HOME/Services/ai-brief"
cd "$HOME/Services/ai-brief"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p .runtime
git push --dry-run origin HEAD:main
```

마지막 명령으로 저장소 쓰기 권한과 무인 인증 준비를 확인합니다. Gateway에서도 같은 사용자의 인증 저장소에 접근할 수 있어야 합니다. 이미 SSH deploy key를 운영하고 있다면 [Oracle 문서의 저장소 전용 Git 인증](deployment-oracle.md#이-저장소-전용-git-인증)을 Mac 경로에 맞춰 사용해도 됩니다.

이 폴더에서는 개발 중인 코드나 수동 편집한 데이터를 남기지 않습니다. 수집기는 변경 중인 파일을 덮어쓰지 않도록 dirty checkout에서 중단합니다. 코드 개발은 별도 clone/worktree에서 하고 main에 반영합니다.

## 3. OpenRouter 설정과 발행 전 확인

```bash
cd "$HOME/Services/ai-brief"
cp .env.example .env
chmod 600 .env
nano .env
```

이미 `.env`가 있다면 복사하지 말고 기존 파일을 편집하세요. 아래 값에서 키를 본인 값으로 입력합니다. GitHub Secrets의 키는 clone으로 내려오지 않습니다.

```dotenv
OPENROUTER_API_KEY=본인의_OpenRouter_키
OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash
MAX_SUMMARIES_PER_RUN=1
ENGLISH_SUMMARIES_PER_RUN=10
BRIEFING_MAX_TOKENS=50000
SITE_URL=https://news-curatiing.pages.dev
```

`.env`는 Python이 직접 읽으며 Git에서 제외됩니다. `MAX_SUMMARIES_PER_RUN=0`이면 모든 유료 요약이 꺼집니다. OpenClaw 자체의 대화 모델 설정과 이 수집기의 OpenRouter 설정은 별개입니다.

GitHub 예약을 아직 유지한 상태에서는 공개 파일을 쓰지 않는 점검부터 합니다.

```bash
.venv/bin/python -m collector validate
.venv/bin/python -m collector collect --dry-run
git status --short
```

`--dry-run`은 모델을 호출하거나 수집 결과를 공개 데이터에 저장하지 않습니다. Mac의 네트워크·인증·본문 접근을 확인한 다음 실행 주체를 전환합니다.

## 4. GitHub 예약에서 Mac으로 전환

GitHub 저장소 **Settings → Secrets and variables → Actions → Variables → COLLECTION_ENABLED**를 `false`로 바꿉니다. CLI를 쓰려면 다음과 같습니다.

```bash
gh variable set COLLECTION_ENABLED --body false --repo jrkim3888/news_curatiing
gh run list --repo jrkim3888/news_curatiing --workflow collect.yml --limit 5
```

이미 시작했거나 대기 중인 수집·발행 작업은 변수 변경만으로 종료되지 않습니다. 모두 끝난 것을 확인한 뒤 Mac에서 실행합니다. 코드 CI와 Cloudflare Pages Git 연결은 계속 사용합니다.

처음에는 유료 요약 없이 실제 수집·commit·push 경로를 확인합니다.

```bash
cd "$HOME/Services/ai-brief"
NO_LLM=true NEWS_MODE=collect /bin/bash ops/run-collector.sh
```

새 데이터가 있으면 커밋 하나가 생기고 Pages가 배포합니다. 변경이 없으면 커밋하지 않는 것이 정상입니다. 이후 아래 명령으로 유료 요약과 브리핑을 확인할 수 있습니다.

```bash
NEWS_MODE=collect /bin/bash ops/run-collector.sh
NEWS_MODE=brief /bin/bash ops/run-collector.sh
```

이미 발행한 날짜는 `already_published`로 건너뜁니다. 같은 날짜를 덮어쓰지 않습니다. 실패한 날짜를 명시적으로 복구할 때만 다음처럼 한 차례 재시도하세요.

```bash
NEWS_MODE=brief BRIEFING_DATE=2026-10-28 RETRY_FAILED_BRIEFING=true /bin/bash ops/run-collector.sh
```

날짜와 재시도 옵션을 정기 작업에 넣지 마세요. Mac 전환을 중단해야 한다면 Mac 예약을 먼저 끄고 실행 중인 작업이 끝난 뒤 `COLLECTION_ENABLED=true`로 되돌립니다.

## 5. OpenClaw에 예약 두 개 등록

Gateway가 이 Mac에서 실행 중인 것을 확인하세요. 다른 서버의 Gateway에 연결하면 그 서버에서 명령이 실행됩니다. 아래 명령은 **각각 한 번만** 등록하고 반환되는 작업 ID를 보관합니다. 기존 작업이 있으면 `automations list`로 확인해 수정하거나 재활성화하세요.

```bash
cd "$HOME/Services/ai-brief"

openclaw automations create "0 0,6,12,18 * * *" \
  --name "AI Brief - collect" \
  --tz "Asia/Seoul" --exact \
  --command-argv '["/bin/bash","ops/run-collector.sh"]' \
  --command-cwd "$PWD" \
  --command-env NEWS_MODE=collect \
  --timeout-seconds 3000 --no-output-timeout-seconds 3000 \
  --no-deliver

openclaw automations create "30 6 * * *" \
  --name "AI Brief - daily briefing" \
  --tz "Asia/Seoul" --exact \
  --command-argv '["/bin/bash","ops/run-collector.sh"]' \
  --command-cwd "$PWD" \
  --command-env NEWS_MODE=brief \
  --timeout-seconds 3000 --no-output-timeout-seconds 3000 \
  --no-deliver

openclaw automations list
```

수집은 KST 00·06·12·18시, 브리핑은 KST 06:30입니다. `--exact`로 예약 분산을 끄고, Mac의 시스템 시간대와 관계없이 `Asia/Seoul`로 평가합니다. `--no-deliver`는 외부 메신저 전송 없이 실행 이력에만 결과를 남깁니다. 수집기의 `.env`가 요약을 제어하므로 예약마다 별도 에이전트 모델을 지정하지 않습니다.

두 작업은 같은 체크아웃의 `flock`을 공유합니다. 이전 작업이 계속 실행 중이면 새 실행은 건너뜁니다. 실행이 30분을 넘거나 Mac이 잠든 경우 발행이 늦거나 누락될 수 있으므로 운영 이력을 확인해야 합니다. 스크립트에 Linux 전용 `timeout`을 덧붙일 필요는 없으며 OpenClaw의 50분 제한을 사용합니다.

등록 후 OpenClaw를 통해서도 수동 실행해 서비스 환경의 Git 인증과 경로를 확인합니다. `JOB_ID`는 앞에서 반환된 실제 ID로 바꾸세요.

```bash
openclaw automations run JOB_ID
openclaw automations runs JOB_ID --limit 5
```

첫 명령의 접수 성공은 배치 완료가 아닙니다. 실행 이력에서 종료 성공과 로그를 확인하고 GitHub 데이터 커밋·Pages 배포까지 확인합니다. 중지할 때는 `openclaw automations disable JOB_ID`, 다시 켤 때는 `openclaw automations enable JOB_ID`를 사용합니다.

## 6. 결과와 운영 확인

- 공개 데이터: `data/news/YYYY-MM.json`, `data/briefings/YYYY-MM.json`, `data/catalog.json`.
- 마지막 실행 진단: `.runtime/last-run.json`. 수집과 브리핑이 공유하는 최신 보고서이므로 실행별 기록은 OpenClaw에서도 확인합니다.
- 재요청 대기 상태: `.runtime/summary-attempts.json`. 지우면 실패 요청의 재호출 방지가 사라질 수 있습니다.
- 스케줄러 로그: `openclaw automations runs JOB_ID --limit 5`.
- 재부팅 후: 사용자 로그인, `openclaw gateway status`, 예약 활성화 상태, Mac 잠자기 설정과 네트워크를 확인합니다.

코드의 Apple Silicon/Intel Homebrew 경로를 지원하고, 체크섬 계산은 Python 표준 라이브러리를 사용합니다. `flock`이 없거나 잠금 자체가 실패하면 성공으로 처리하지 않고 오류로 종료합니다. 기존 Linux 운영도 같은 스크립트를 사용합니다. 이 저장소의 자동 테스트는 Linux에서 수행했으며 실제 Mac·OpenClaw 설치와 인증은 해당 Mac에서 위 순서로 확인해야 합니다.

공식 참고: [OpenClaw 설치](https://docs.openclaw.ai/start/getting-started), [예약 CLI](https://docs.openclaw.ai/cli/cron), [command 작업](https://docs.openclaw.ai/automation/cron-jobs/payloads#command-payloads), [macOS flock](https://github.com/discoteq/flock).
