#!/usr/bin/env bash
# Run in a dedicated collector checkout (GitHub Actions or an unprivileged VM user).
set -Eeuo pipefail
umask 077
export PATH="/usr/local/bin:/usr/bin:/bin"
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p .runtime
exec 9>.runtime/collector.lock
if ! flock -n 9; then
  echo '[skip] Another collection is running.'
  exit 0
fi
echo "[start] $(date -u +%FT%TZ)"
BRANCH="${NEWS_BRANCH:-main}"
if [[ "$(git branch --show-current)" != "$BRANCH" ]]; then
  echo "[error] Collector must be on $BRANCH."
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo '[error] Tracked local changes exist. Inspect them before retrying.'
  exit 1
fi
git fetch --quiet origin "$BRANCH"
git merge --ff-only --no-edit "origin/$BRANCH"
# Retry a previously committed batch after a push failure. Never force-push or discard it.
if [[ "$(git rev-list --count "origin/$BRANCH..HEAD")" != 0 ]]; then
  if [[ "$(git diff --name-only "origin/$BRANCH" HEAD)" != 'data/news.json' ]]; then
    echo '[error] Unpublished changes include code; operator review required.'
    exit 1
  fi
  git push origin "HEAD:$BRANCH"
  echo '[done] Recovered the pending data commit; collection resumes next run.'
  exit 0
fi
if [[ ! -x .venv/bin/python ]]; then
  echo '[error] Run python3 -m venv .venv and install requirements.txt first.'
  exit 1
fi
NEW_REQUIREMENTS="$(sha256sum requirements.txt | cut -d ' ' -f1)"
OLD_REQUIREMENTS="$(cat .runtime/requirements.sha256 2>/dev/null || true)"
if [[ "$NEW_REQUIREMENTS" != "$OLD_REQUIREMENTS" ]]; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf '%s\n' "$NEW_REQUIREMENTS" > .runtime/requirements.sha256
fi
.venv/bin/python -m collector validate
cp data/news.json .runtime/news-before.json
BATCH_COMMITTED=0
restore_on_error() {
  local status=$?
  if [[ "$status" != 0 && "$BATCH_COMMITTED" == 0 ]]; then
    cp .runtime/news-before.json data/news.json
    git restore --staged -- data/news.json
    echo '[error] Batch failed; public data restored. Inspect .runtime/last-run.json and cron logs.'
  fi
  exit "$status"
}
trap restore_on_error EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
.venv/bin/python -u -m collector
.venv/bin/python -m collector validate
if git diff --quiet -- data/news.json; then
  echo '[done] No content changes; no commit or Pages build.'
  exit 0
fi
git add -- data/news.json
git -c user.name='AI Brief Collector' -c user.email='ai-brief-collector@users.noreply.github.com' \
  commit -m "data: refresh AI news $(date -u +%F-%H%M)"
BATCH_COMMITTED=1
git push origin "HEAD:$BRANCH"
echo "[done] $(date -u +%FT%TZ) — data pushed; Pages will build independently."
