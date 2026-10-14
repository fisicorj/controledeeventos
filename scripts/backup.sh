#!/usr/bin/env bash
# Backup do Arena+: banco (pg_dump) + imagens e arquivos privados (media/ e private_media/).
#
# Uso (na pasta do projeto):
#   bash scripts/backup.sh               # instalação com Docker (serviços db e web do docker-compose)
#   bash scripts/backup.sh --sem-docker  # instalação sem Docker: usa pg_dump e as pastas locais, com os dados do .env
#
# Variáveis opcionais:
#   BACKUP_DIR            pasta de destino (padrão: ./backups)
#   KEEP_DAYS             apaga backups mais antigos que isso (padrão: 14)
#   BACKUP_AGE_RECIPIENT  chave pública do age (age1...) ou arquivo com chaves: os arquivos saem criptografados
#                         (.age) e só quem tem a chave privada, guardada FORA do servidor, consegue abrir
#
# Agende uma vez por dia (cron, por exemplo):
#   30 2 * * * cd /opt/arena-events && bash scripts/backup.sh >> backups/backup.log 2>&1
# e copie a pasta de backups para FORA do servidor (outro disco, outro servidor, nuvem). Backup que fica só
# no mesmo disco não protege contra perda do servidor. Teste a restauração de tempos em tempos
# (bash scripts/restore.sh --teste).
set -euo pipefail

cd "$(dirname "$0")/.."
MODE=docker
[ "${1:-}" = "--sem-docker" ] && MODE=local

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
BACKUP_DIR=${BACKUP_DIR:-./backups}
KEEP_DAYS=${KEEP_DAYS:-14}
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"  # o backup tem dados pessoais: só o dono lê
umask 077

DB_FILE="$BACKUP_DIR/arena-db-$STAMP.dump"
FILES_FILE="$BACKUP_DIR/arena-arquivos-$STAMP.tar.gz"

echo "[$(date '+%F %T')] Backup do banco -> $DB_FILE"
if [ "$MODE" = docker ]; then
  docker compose exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$DB_FILE.parcial"
else
  PGPASSWORD="${DB_PASSWORD:-}" pg_dump -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
    -Fc > "$DB_FILE.parcial"
fi
mv "$DB_FILE.parcial" "$DB_FILE"  # só vira .dump se terminou sem erro

echo "[$(date '+%F %T')] Backup de media/ e private_media/ -> $FILES_FILE"
if [ "$MODE" = docker ]; then
  docker compose exec -T web tar -C /app -czf - media private_media > "$FILES_FILE.parcial"
else
  mkdir -p media private_media
  tar -czf "$FILES_FILE.parcial" media private_media
fi
mv "$FILES_FILE.parcial" "$FILES_FILE"

# Confere se os arquivos abrem (um backup corrompido só seria descoberto na hora de restaurar).
if [ "$MODE" = docker ]; then
  docker compose exec -T db pg_restore --list < "$DB_FILE" > /dev/null
else
  pg_restore --list "$DB_FILE" > /dev/null
fi
tar -tzf "$FILES_FILE" > /dev/null

# Criptografia (recomendada antes de copiar para fora do servidor): o backup tem dados pessoais.
if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  command -v age > /dev/null || { echo "Instale o age (https://age-encryption.org) ou deixe BACKUP_AGE_RECIPIENT vazio." >&2; exit 1; }
  if [ -f "$BACKUP_AGE_RECIPIENT" ]; then RECIPIENT=(-R "$BACKUP_AGE_RECIPIENT"); else RECIPIENT=(-r "$BACKUP_AGE_RECIPIENT"); fi
  for f in "$DB_FILE" "$FILES_FILE"; do
    age "${RECIPIENT[@]}" -o "$f.age.parcial" "$f"
    mv "$f.age.parcial" "$f.age"
    rm -f "$f"
  done
  DB_FILE="$DB_FILE.age"
  FILES_FILE="$FILES_FILE.age"
fi
(cd "$BACKUP_DIR" && sha256sum "$(basename "$DB_FILE")" "$(basename "$FILES_FILE")" > "arena-$STAMP.sha256")

find "$BACKUP_DIR" -maxdepth 1 -name 'arena-*' -type f -mtime +"$KEEP_DAYS" -delete
echo "[$(date '+%F %T')] Pronto: $(du -h "$DB_FILE" | cut -f1) de banco, $(du -h "$FILES_FILE" | cut -f1) de arquivos."
