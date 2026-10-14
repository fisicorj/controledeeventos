#!/usr/bin/env bash
# Restauração do backup feito por bash scripts/backup.sh.
#
# Teste de restauração (recomendado uma vez por mês; não mexe nos dados em uso):
#   bash scripts/restore.sh --teste backups/arena-db-AAAAMMDD-HHMMSS.dump
#   Restaura num banco temporário (arena_restore_test), mostra quantas linhas têm as tabelas principais e apaga.
#
# Restauração de verdade (substitui o banco e os arquivos atuais; pare o site antes):
#   docker compose stop web worker
#   bash scripts/restore.sh backups/arena-db-AAAAMMDD-HHMMSS.dump backups/arena-arquivos-AAAAMMDD-HHMMSS.tar.gz
#   docker compose start web worker
#
# Sem Docker, acrescente --sem-docker como primeira opção.
#
# Backup criptografado (arquivos .age): informe a chave privada em BACKUP_AGE_IDENTITY (caminho do arquivo).
set -euo pipefail

cd "$(dirname "$0")/.."
MODE=docker
if [ "${1:-}" = "--sem-docker" ]; then MODE=local; shift; fi
TEST=0
if [ "${1:-}" = "--teste" ]; then TEST=1; shift; fi
DB_FILE=${1:?Informe o arquivo .dump}
FILES_FILE=${2:-}

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
DB_NAME=${DB_NAME:-arena_events}
# Backup e restauração usam o dono das tabelas quando há dois papéis (README, "Banco: dois papéis").
DB_USER=${DB_MIGRATE_USER:-${DB_USER:-arena_user}}
DB_PASSWORD=${DB_MIGRATE_PASSWORD:-${DB_PASSWORD:-}}

BASE="$(dirname "$DB_FILE")"
STAMP_NAME=$(basename "${DB_FILE%.age}" .dump | sed 's/^arena-db-//')
SUMS=$(ls "$BASE"/arena-"$STAMP_NAME".sha256 2>/dev/null || true)
if [ -n "$SUMS" ]; then
  (cd "$BASE" && sha256sum --check --ignore-missing "$(basename "$SUMS")")
fi

# Backup criptografado: abre numa pasta temporária só deste usuário, apagada no fim.
WORK=$(mktemp -d)
chmod 700 "$WORK"
cleanup_work() { rm -rf "$WORK"; }
trap cleanup_work EXIT
decrypt() {  # devolve o caminho do arquivo pronto para usar
  local file=$1
  if [ "${file%.age}" = "$file" ]; then echo "$file"; return; fi
  : "${BACKUP_AGE_IDENTITY:?Informe a chave privada do age em BACKUP_AGE_IDENTITY para abrir $file}"
  local out="$WORK/$(basename "${file%.age}")"
  age -d -i "$BACKUP_AGE_IDENTITY" -o "$out" "$file"
  echo "$out"
}
DB_FILE=$(decrypt "$DB_FILE")
if [ -n "$FILES_FILE" ]; then FILES_FILE=$(decrypt "$FILES_FILE"); fi

psql_cmd() {  # psql no banco indicado (1º argumento); o resto vai para o psql
  local db=$1; shift
  if [ "$MODE" = docker ]; then
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$db" "$@"
  else
    PGPASSWORD="${DB_PASSWORD:-}" psql -v ON_ERROR_STOP=1 -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" \
      -U "$DB_USER" -d "$db" "$@"
  fi
}

restore_into() {
  local db=$1
  if [ "$MODE" = docker ]; then
    docker compose exec -T db pg_restore --clean --if-exists --no-owner -U "$DB_USER" -d "$db" < "$DB_FILE"
  else
    PGPASSWORD="${DB_PASSWORD:-}" pg_restore --clean --if-exists --no-owner -h "${DB_HOST:-localhost}" \
      -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$db" "$DB_FILE"
  fi
}

if [ "$TEST" = 1 ]; then
  TEST_DB=arena_restore_test
  psql_cmd "$DB_NAME" -q -c "DROP DATABASE IF EXISTS $TEST_DB" -c "CREATE DATABASE $TEST_DB"
  trap 'psql_cmd "$DB_NAME" -q -c "DROP DATABASE IF EXISTS $TEST_DB"; cleanup_work' EXIT
  restore_into "$TEST_DB"
  echo "Restauração de teste concluída. Linhas por tabela:"
  psql_cmd "$TEST_DB" -c "
    SELECT 'contas' AS tabela, count(*) FROM accounts_user
    UNION ALL SELECT 'atividades', count(*) FROM activities_activity
    UNION ALL SELECT 'inscrições', count(*) FROM registrations_registration
    UNION ALL SELECT 'presenças', count(*) FROM attendance_attendance
    UNION ALL SELECT 'certificados', count(*) FROM certificates_certificate
    UNION ALL SELECT 'auditoria de certificados', count(*) FROM certificates_certificateevent
    UNION ALL SELECT 'migrações aplicadas', count(*) FROM django_migrations"
  if [ -n "$FILES_FILE" ]; then
    echo "Arquivos no backup: $(tar -tzf "$FILES_FILE" | grep -vc '/$')"
  fi
  exit 0
fi

read -r -p "Isto SUBSTITUI o banco $DB_NAME${FILES_FILE:+ e os arquivos} pelos do backup. Digite RESTAURAR para continuar: " answer
[ "$answer" = RESTAURAR ] || { echo "Cancelado."; exit 1; }
restore_into "$DB_NAME"
echo "Banco restaurado."
if [ -n "$FILES_FILE" ]; then
  if [ "$MODE" = docker ]; then
    docker compose run --rm --no-deps -T web tar -C /app -xzf - < "$FILES_FILE"
  else
    tar -xzf "$FILES_FILE"
  fi
  echo "Arquivos restaurados."
fi
