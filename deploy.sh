#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

origin="${1:-}"
origin="${origin%/}"
if [[ ! "$origin" =~ ^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    printf 'Usage: bash deploy.sh http://YOUR_VPS_IP\n'
    printf '   or: bash deploy.sh https://your-domain.example\n' >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    printf 'Install Docker Engine and the Docker Compose plugin first. See VPS.md.\n' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    printf 'Docker is not running or this user cannot access it. Run with appropriate Docker permissions.\n' >&2
    exit 1
fi

if [[ ! -f .env.vps ]]; then
    umask 077
    db_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
    staff_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
    secure=false
    [[ "$origin" == https://* ]] && secure=true
    cat > .env.vps <<EOF
APP_ORIGIN=$origin
COOKIE_SECURE=$secure
POSTGRES_PASSWORD=$db_password
STAFF_PASSWORD=$staff_password
TELEGRAM_BOT_TOKEN=
ASSISTANT_LLM_ENABLED=false
OPENAI_API_KEY=
OPENAI_MODEL=
EOF
    printf 'Created .env.vps with new server-specific credentials.\n'
elif ! grep -Fqx "APP_ORIGIN=$origin" .env.vps; then
    printf 'Existing .env.vps has another APP_ORIGIN. Update APP_ORIGIN and COOKIE_SECURE there before changing the URL.\n' >&2
    exit 1
fi

compose=(docker compose --env-file .env.vps -f compose.vps.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --build --wait --wait-timeout 240
printf '\nWeb: %s/\nMini app: %s/mini/\nStaff: %s/staff\n' "$origin" "$origin" "$origin"
printf 'The staff password is stored in STAFF_PASSWORD in .env.vps.\n'
"${compose[@]}" ps
