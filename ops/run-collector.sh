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
MODE="${NEWS_MODE:-collect}"
case "$MODE" in collect|brief) ;; *) echo '[error] NEWS_MODE must be collect or brief.'; exit 1 ;; esac
COLLECTOR_ARGS=("$MODE")
if [[ "$MODE" == collect ]]; then
  COLLECTOR_ARGS+=(--summarize-english)
fi
case "${NO_LLM:-false}" in
  true) COLLECTOR_ARGS+=(--no-llm) ;;
  false) ;;
  *) echo '[error] NO_LLM must be true or false.'; exit 1 ;;
esac
if [[ -n "${BRIEFING_DATE:-}" ]]; then
  COLLECTOR_ARGS+=(--briefing-date "$BRIEFING_DATE")
fi
case "${RETRY_FAILED_BRIEFING:-false}" in
  true) COLLECTOR_ARGS+=(--retry-failed-briefing) ;;
  false) ;;
  *) echo '[error] RETRY_FAILED_BRIEFING must be true or false.'; exit 1 ;;
esac
if [[ "$(git branch --show-current)" != "$BRANCH" ]]; then
  echo "[error] Collector must be on $BRANCH."
  exit 1
fi
if [[ -f .runtime/archive-transaction/manifest.json && -x .venv/bin/python ]]; then
  .venv/bin/python -m collector recover
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" || -n "$(git ls-files --others --exclude-standard -- data)" ]]; then
  echo '[error] Local changes or untracked data files exist. Inspect them before retrying.'
  exit 1
fi
git fetch --quiet origin "$BRANCH"
git merge --ff-only --no-edit "origin/$BRANCH"
# Retry a previously committed batch after a push failure. Never force-push or discard it.
if [[ "$(git rev-list --count "origin/$BRANCH..HEAD")" != 0 ]]; then
  while IFS= read -r path; do
    case "$path" in
      data/catalog.json|data/sources.json|data/news.json|data/briefings.json|data/news/[0-9][0-9][0-9][0-9]-[0-9][0-9].json|data/briefings/[0-9][0-9][0-9][0-9]-[0-9][0-9].json) ;;
      *) echo '[error] Unpublished changes include code; operator review required.'; exit 1 ;;
    esac
  done < <(git diff --name-only "origin/$BRANCH" HEAD)
  git push origin "HEAD:$BRANCH"
  echo '[done] Recovered the pending data commit.'
  if [[ "$MODE" == collect ]]; then exit 0; fi
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
.venv/bin/python -m collector validate --incremental
# The clean Git HEAD is the batch checkpoint; only changed shards need restoring.
BATCH_COMMITTED=0
restore_on_error() {
  local status=$?
  if [[ "$status" != 0 && "$BATCH_COMMITTED" == 0 ]]; then
    if [[ -f .runtime/archive-transaction/manifest.json ]]; then
      if ! .venv/bin/python -m collector recover; then
        echo '[error] Archive recovery needs inspection; public files were left intact.'
        exit "$status"
      fi
    fi
    git restore --source=HEAD --staged --worktree -- data
    while IFS= read -r -d '' path; do
      case "$path" in
        data/catalog.json|data/sources.json|data/news/[0-9][0-9][0-9][0-9]-[0-9][0-9].json|data/briefings/[0-9][0-9][0-9][0-9]-[0-9][0-9].json) rm -- "$path" ;;
      esac
    done < <(git ls-files --others --exclude-standard -z -- data)
    echo '[error] Batch failed; public data restored. Inspect .runtime/last-run.json and cron logs.'
  fi
  exit "$status"
}
trap restore_on_error EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
.venv/bin/python -u -m collector "${COLLECTOR_ARGS[@]}"
.venv/bin/python -m collector validate --incremental
if git diff --quiet -- data && [[ -z "$(git ls-files --others --exclude-standard -- data)" ]]; then
  echo '[done] No content changes; no commit or Pages build.'
  exit 0
fi
# Validate names before staging, including files first created in a new month.
while IFS= read -r path; do
  case "$path" in
    data/catalog.json|data/sources.json|data/news/[0-9][0-9][0-9][0-9]-[0-9][0-9].json|data/briefings/[0-9][0-9][0-9][0-9]-[0-9][0-9].json) ;;
    *) echo '[error] Unexpected public output; operator review required.'; exit 1 ;;
  esac
done < <(git status --porcelain --untracked-files=all -- data | cut -c4-)
git add -A -- data
git -c user.name='AI Brief Collector' -c user.email='ai-brief-collector@users.noreply.github.com' \
  commit -m "data: refresh AI news $(date -u +%F-%H%M)"
BATCH_COMMITTED=1
git push origin "HEAD:$BRANCH"
echo "[done] $(date -u +%FT%TZ) — data pushed; Pages will build independently."
