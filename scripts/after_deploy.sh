#!/usr/bin/env bash
# scripts/after_deploy.sh <stage> <port>
#
# Run on the host after a deploy, from the synced checkout: is the new code
# answering, and is the stage's database on the newest migration? (CHY-322)
#
# Until 0.2.1 the deploy jobs ran build, migrations and `up -d` in one ssh
# string without `set -e`; the job's exit code was that of `up -d`, so it
# reported success after a failed migration (code review R7-01). `/v1/health`
# alone proves nothing about the database either: the version it names comes
# from pyproject.toml, not from schema_migrations. This script checks both,
# from what the deploy just synced, and fails the job on any difference.
#
# Read-only: one GET on the API, one SELECT as the database owner.
set -euo pipefail

stage="${1:?usage: after_deploy.sh STAGE PORT}"
port="${2:?usage: after_deploy.sh STAGE PORT}"

expected_version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
expected_migration="$(ls migrations | sed -n 's/^\([0-9][0-9][0-9]\)_.*\.sql$/\1/p' | sort -n | tail -n 1)"
expected_migration=$((10#$expected_migration))

# The new container needs a moment; a stage that never answers fails here.
# AFTER_DEPLOY_TRIES only exists for the tests (tests/test_after_deploy.py).
tries="${AFTER_DEPLOY_TRIES:-30}"
health=""
for i in $(seq 1 "$tries"); do
  if health="$(curl -fsS "http://127.0.0.1:${port}/v1/health")"; then
    break
  fi
  health=""
  if [ "$i" -lt "$tries" ]; then sleep 2; fi
done
if [ -z "$health" ]; then
  echo "after_deploy: ${stage}: /v1/health on port ${port} did not answer after ${tries} tries" >&2
  exit 1
fi
version="$(printf '%s' "$health" | sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p')"

migration="$(docker exec cohereny-postgres psql -U cohereny -d "cohereny_${stage}" -tAc \
  "SELECT coalesce(max(version), 0) FROM schema_migrations")"
migration="$(printf '%s' "$migration" | tr -d '[:space:]')"

echo "after_deploy: ${stage}: version ${version:-?} (expected ${expected_version}), migration ${migration:-?} (expected ${expected_migration})"
if [ "$version" != "$expected_version" ]; then
  echo "after_deploy: ${stage}: the API answers with version ${version:-none}, the deploy brought ${expected_version}" >&2
  exit 1
fi
if [ "$migration" != "$expected_migration" ]; then
  echo "after_deploy: ${stage}: the database is on migration ${migration:-none}, the deploy brought ${expected_migration}" >&2
  exit 1
fi
