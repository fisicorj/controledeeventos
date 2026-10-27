#!/usr/bin/env bash
# =============================================================================
# arena-events: infraestrutura AWS (ambiente de testes por padrão; STAGE=prod para outro)
# Região us-east-1 | ECS Fargate (serviços web e worker) + RDS PostgreSQL + EFS para os arquivos enviados
#
# USO (WSL, Linux ou Git Bash):
#   chmod +x infra/setup.sh
#   ./infra/setup.sh
#
# Variáveis opcionais (antes do comando, ex.: ALLOWED_CIDR=0.0.0.0/0 ./infra/setup.sh):
#   ALLOWED_CIDR  Quem pode abrir o site. Padrão: só o seu IP atual (/32). Use 0.0.0.0/0 para abrir a todos,
#                 de preferência só com HTTPS (DOMAIN + CERT_ARN).
#   CLOUDFRONT    1 põe o CloudFront na frente do ALB, aberto a todos (o ALB passa a aceitar só o seu CloudFront,
#                 conferido por um cabeçalho secreto, e o WAF, se ligado, vai para o CloudFront). Sem domínio, o site
#                 fica em https://dxxxx.cloudfront.net. Com DOMAIN e CERT_ARN, fica no seu domínio, e o trecho do
#                 CloudFront até o ALB também passa a ser HTTPS (o certificado precisa ser da região us-east-1).
#   DOMAIN        Domínio do site (ex.: eventos.exemplo.br). Com CERT_ARN, liga o HTTPS.
#   CERT_ARN      ARN de um certificado do ACM (us-east-1) válido para DOMAIN.
#   REGION        Padrão us-east-1.
#   DB_CLASS      Tamanho do RDS na criação. Padrão db.t4g.micro (mais barato que o db.t3.micro).
#   STAGE         Nome do ambiente (padrão test). Ex.: STAGE=prod cria outro ambiente, separado deste, com
#                 nomes, segredos, banco e arquivos próprios.
#   ALERT_EMAIL   E-mail que recebe os alarmes (site fora do ar, erros 5xx, banco sem espaço ou sobrecarregado).
#                 A AWS manda um e-mail de confirmação: clique no link para começar a receber.
#   DB_BACKUP_DAYS          Dias de backup automático do banco. Padrão 7.
#   DB_DELETION_PROTECTION  1 impede apagar o banco por engano. Padrão 1 fora do ambiente test.
#   HSTS_SECONDS  Com HTTPS: por quanto tempo o navegador só aceita HTTPS no domínio. Padrão 86400 (1 dia);
#                 aumente (ex.: 31536000) depois de confirmar que o HTTPS está estável.
#   WEB_COUNT     Quantas tarefas do site (web) rodam ao mesmo tempo. Padrão 1. O worker (e-mails, lembretes,
#                 faxina) é um serviço à parte, sempre com 1 tarefa.
#   WAF           1 liga o firewall de aplicação (AWS WAF) no ALB: regras gerenciadas da AWS contra ataques
#                 comuns e IPs maliciosos, mais um limite de requisições por IP. Cerca de US$ 8/mês. Padrão: ligado
#                 quando o site está aberto a todos (ALLOWED_CIDR=0.0.0.0/0 ou CLOUDFRONT=1); 0 desliga e remove.
#   CF_ORIGIN_TIMEOUT  Com CLOUDFRONT=1: segundos que o CloudFront espera a resposta do site (padrão 60, o máximo
#                 sem pedir à AWS). Relatórios em PDF de eventos grandes podem demorar mais: peça o aumento da
#                 cota "Response timeout per origin" no Service Quotas e rode com até 180.
#   ACCESS_LOGS   1 (padrão) guarda os registros de acesso do ALB e do CloudFront (quem pediu o quê, de onde,
#                 com que resposta) num bucket S3 do ambiente, para investigar incidentes; 0 desliga.
#   LOG_DAYS      Dias que esses registros ficam no bucket antes de serem apagados sozinhos. Padrão 60.
#   WAF_RATE_LIMIT  Requisições por IP em 5 minutos antes do bloqueio (padrão 3000: um laboratório inteiro
#                 sai pelo mesmo IP, e cada página carrega várias imagens e arquivos).
#
# PRÉ-REQUISITOS: aws cli configurado (aws configure), docker rodando, python3 e curl.
#
# Pode rodar de novo quantas vezes quiser: o que já existe é reaproveitado e a aplicação é atualizada.
# =============================================================================

set -euo pipefail

APP_NAME="arena-events"
STAGE="${STAGE:-test}"
if ! [[ "${STAGE}" =~ ^[a-z][a-z0-9-]{0,11}$ ]]; then
  echo "STAGE deve ter letras minúsculas, números e hífen (até 12 caracteres), começando com letra." >&2
  exit 1
fi
REGION="${REGION:-us-east-1}"
PREFIX="${APP_NAME}-${STAGE}"
SSM_PATH="/${APP_NAME}/${STAGE}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
DB_BACKUP_DAYS="${DB_BACKUP_DAYS:-7}"
if [ "${STAGE}" = "test" ]; then DB_DELETION_PROTECTION="${DB_DELETION_PROTECTION:-0}"
else DB_DELETION_PROTECTION="${DB_DELETION_PROTECTION:-1}"; fi
HSTS_SECONDS="${HSTS_SECONDS:-86400}"
WEB_COUNT="${WEB_COUNT:-1}"
WAF_RATE_LIMIT="${WAF_RATE_LIMIT:-3000}"
CF_ORIGIN_TIMEOUT="${CF_ORIGIN_TIMEOUT:-60}"
ACCESS_LOGS="${ACCESS_LOGS:-1}"
LOG_DAYS="${LOG_DAYS:-60}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

DB_NAME="arena_events"
DB_CLASS="${DB_CLASS:-db.t4g.micro}"
DB_APP_USER="arena_app"          # papel da aplicação: sem dono das tabelas (README, "Banco: dois papéis")
DB_MASTER_DEFAULT="arena_admin"  # usuário mestre do RDS: só nas migrações
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
APP_UID=1000                     # o mesmo do Dockerfile (useradd --uid 1000 app)

DOMAIN="${DOMAIN:-}"
CERT_ARN="${CERT_ARN:-}"
USE_HTTPS=false
if [ -n "${DOMAIN}" ] && [ -n "${CERT_ARN}" ]; then USE_HTTPS=true; fi
CLOUDFRONT="${CLOUDFRONT:-0}"
if [ "${CLOUDFRONT}" = "1" ] && [ "${USE_HTTPS}" = true ] && [[ "${CERT_ARN}" != arn:aws:acm:us-east-1:* ]]; then
  echo "Com CLOUDFRONT=1, o certificado (CERT_ARN) precisa ser do ACM em us-east-1, a região do CloudFront." >&2
  exit 1
fi
if [ "${CLOUDFRONT}" = "1" ] && [ "${USE_HTTPS}" = true ] && [ "${REGION}" != "us-east-1" ]; then
  echo "CLOUDFRONT=1 com domínio usa o mesmo certificado no CloudFront e no ALB: rode com REGION=us-east-1." >&2
  exit 1
fi
# Porta em que o ALB recebe o CloudFront: 443 com domínio (HTTPS também nesse trecho), 80 sem domínio.
CF_PORT=80
[ "${USE_HTTPS}" = true ] && CF_PORT=443
ORIGIN_HEADER="X-Origin-Verify"
if [ "${CLOUDFRONT}" = "1" ]; then
  ALLOWED_CIDR="0.0.0.0/0"  # aberto a todos pelo CloudFront; o ALB aceita só o CloudFront
elif [ -z "${ALLOWED_CIDR:-}" ]; then
  MY_IP=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
  ALLOWED_CIDR="${MY_IP}/32"
fi

aws_q() { aws "$@" --region "${REGION}"; }
none() { [ -z "$1" ] || [ "$1" = "None" ]; }
if [ -z "${WAF:-}" ]; then
  WAF=0
  [ "${ALLOWED_CIDR}" = "0.0.0.0/0" ] && WAF=1
fi

echo "=============================================="
echo " arena-events :: deploy na AWS"
echo " Conta   : ${AWS_ACCOUNT_ID}"
echo " Região  : ${REGION}"
echo " Ambiente: ${STAGE}
 Acesso  : ${ALLOWED_CIDR}  | HTTPS: ${USE_HTTPS}  | CloudFront: ${CLOUDFRONT}  | WAF: ${WAF}"
echo "=============================================="

# ── 1. ECR ────────────────────────────────────────────────────────────────────
echo "[1/10] ECR..."
ECR_URI=$(aws_q ecr describe-repositories --repository-names "${PREFIX}" \
  --query 'repositories[0].repositoryUri' --output text 2>/dev/null || true)
if none "${ECR_URI}"; then
  ECR_URI=$(aws_q ecr create-repository --repository-name "${PREFIX}" \
    --image-scanning-configuration scanOnPush=true \
    --query 'repository.repositoryUri' --output text)
fi
# Tags imutáveis: uma tag sempre aponta para a mesma imagem (o deploy sabe exatamente o que está rodando).
aws_q ecr put-image-tag-mutability --repository-name "${PREFIX}" --image-tag-mutability IMMUTABLE > /dev/null
# Guarda só as 15 imagens mais recentes (as antigas custam armazenamento e não servem para nada).
aws_q ecr put-lifecycle-policy --repository-name "${PREFIX}" --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"ultimas 15","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":15},"action":{"type":"expire"}}]}' \
  > /dev/null
echo "   ${ECR_URI}"

# ── 2. Build e push ───────────────────────────────────────────────────────────
echo "[2/10] Build e push da imagem..."
# Certificado de CA do RDS: vai para a imagem, para a conexão com o banco conferir o servidor (verify-full).
mkdir -p "${PROJECT_DIR}/certs"
curl -fsS -o "${PROJECT_DIR}/certs/rds-global-bundle.pem" \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
aws_q ecr get-login-password \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_TAG=$(git -C "${PROJECT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "sem-git")
if ! git -C "${PROJECT_DIR}" diff --quiet HEAD 2>/dev/null; then
  IMAGE_TAG="${IMAGE_TAG}-$(date +%Y%m%d%H%M%S)"  # alterações não commitadas: não reaproveita a tag do commit
fi
FULL_IMAGE="${ECR_URI}:${IMAGE_TAG}"
if aws_q ecr describe-images --repository-name "${PREFIX}" --image-ids "imageTag=${IMAGE_TAG}" > /dev/null 2>&1; then
  echo "   A imagem deste commit já está no ECR: build pulado."
else
  # O Fargate roda em x86: em Mac com chip Apple, sem --platform a imagem sairia ARM.
  docker build --platform linux/amd64 -t "${FULL_IMAGE}" "${PROJECT_DIR}"
  docker push "${FULL_IMAGE}"
fi
echo "   ${FULL_IMAGE}"

# ── 3. VPC, subnets e rotas ───────────────────────────────────────────────────
echo "[3/10] VPC e subnets..."
VPC_ID=$(aws_q ec2 describe-vpcs --filters "Name=tag:Name,Values=${PREFIX}-vpc" \
  --query 'Vpcs[0].VpcId' --output text)
if none "${VPC_ID}"; then
  VPC_ID=$(aws_q ec2 create-vpc --cidr-block "10.0.0.0/16" --query 'Vpc.VpcId' --output text)
  aws_q ec2 create-tags --resources "${VPC_ID}" --tags "Key=Name,Value=${PREFIX}-vpc" > /dev/null
fi
aws_q ec2 modify-vpc-attribute --vpc-id "${VPC_ID}" --enable-dns-support '{"Value":true}'
aws_q ec2 modify-vpc-attribute --vpc-id "${VPC_ID}" --enable-dns-hostnames '{"Value":true}'

create_subnet() {
  local NAME=$1 CIDR=$2 AZ=$3 ID
  ID=$(aws_q ec2 describe-subnets \
    --filters "Name=tag:Name,Values=${NAME}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'Subnets[0].SubnetId' --output text)
  if none "${ID}"; then
    ID=$(aws_q ec2 create-subnet --vpc-id "${VPC_ID}" --cidr-block "${CIDR}" \
      --availability-zone "${REGION}${AZ}" --query 'Subnet.SubnetId' --output text)
    aws_q ec2 create-tags --resources "${ID}" --tags "Key=Name,Value=${NAME}" > /dev/null
  fi
  echo "${ID}"
}
SUBNET_PUB_A=$(create_subnet "${PREFIX}-pub-a" "10.0.1.0/24" "a")
SUBNET_PUB_B=$(create_subnet "${PREFIX}-pub-b" "10.0.2.0/24" "b")
SUBNET_PRIV_A=$(create_subnet "${PREFIX}-priv-a" "10.0.3.0/24" "a")
SUBNET_PRIV_B=$(create_subnet "${PREFIX}-priv-b" "10.0.4.0/24" "b")

IGW_ID=$(aws_q ec2 describe-internet-gateways --filters "Name=tag:Name,Values=${PREFIX}-igw" \
  --query 'InternetGateways[0].InternetGatewayId' --output text)
if none "${IGW_ID}"; then
  IGW_ID=$(aws_q ec2 create-internet-gateway --query 'InternetGateway.InternetGatewayId' --output text)
  aws_q ec2 create-tags --resources "${IGW_ID}" --tags "Key=Name,Value=${PREFIX}-igw" > /dev/null
fi
ATTACHED=$(aws_q ec2 describe-internet-gateways --internet-gateway-ids "${IGW_ID}" \
  --query 'InternetGateways[0].Attachments[0].VpcId' --output text)
if none "${ATTACHED}"; then
  aws_q ec2 attach-internet-gateway --internet-gateway-id "${IGW_ID}" --vpc-id "${VPC_ID}"
fi

# Rota pública conferida a cada execução (uma execução interrompida no meio não deixa as subnets sem saída).
RT_ID=$(aws_q ec2 describe-route-tables \
  --filters "Name=tag:Name,Values=${PREFIX}-rt-pub" "Name=vpc-id,Values=${VPC_ID}" \
  --query 'RouteTables[0].RouteTableId' --output text)
if none "${RT_ID}"; then
  RT_ID=$(aws_q ec2 create-route-table --vpc-id "${VPC_ID}" --query 'RouteTable.RouteTableId' --output text)
  aws_q ec2 create-tags --resources "${RT_ID}" --tags "Key=Name,Value=${PREFIX}-rt-pub" > /dev/null
fi
HAS_ROUTE=$(aws_q ec2 describe-route-tables --route-table-ids "${RT_ID}" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId" --output text)
if none "${HAS_ROUTE}"; then
  aws_q ec2 create-route --route-table-id "${RT_ID}" --destination-cidr-block 0.0.0.0/0 \
    --gateway-id "${IGW_ID}" > /dev/null
fi
for SUBNET in "${SUBNET_PUB_A}" "${SUBNET_PUB_B}"; do
  read -r CURRENT ASSOC < <(aws_q ec2 describe-route-tables \
    --filters "Name=association.subnet-id,Values=${SUBNET}" \
    --query "RouteTables[0].[RouteTableId, Associations[?SubnetId=='${SUBNET}'].RouteTableAssociationId | [0]]" \
    --output text)
  if none "${CURRENT:-}"; then
    aws_q ec2 associate-route-table --route-table-id "${RT_ID}" --subnet-id "${SUBNET}" > /dev/null
  elif [ "${CURRENT}" != "${RT_ID}" ]; then
    aws_q ec2 replace-route-table-association --association-id "${ASSOC}" --route-table-id "${RT_ID}" > /dev/null
  fi
done
echo "   VPC=${VPC_ID}"

# ── 4. Security Groups ────────────────────────────────────────────────────────
echo "[4/10] Security Groups..."
create_sg() {
  local NAME=$1 DESC=$2 ID
  ID=$(aws_q ec2 describe-security-groups \
    --filters "Name=tag:Name,Values=${NAME}" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text)
  if none "${ID}"; then
    ID=$(aws_q ec2 create-security-group --group-name "${NAME}" --description "${DESC}" \
      --vpc-id "${VPC_ID}" --query 'GroupId' --output text)
    aws_q ec2 create-tags --resources "${ID}" --tags "Key=Name,Value=${NAME}" > /dev/null
  fi
  echo "${ID}"
}
SG_ALB=$(create_sg "${PREFIX}-sg-alb" "ALB arena-events")
SG_APP=$(create_sg "${PREFIX}-sg-app" "App arena-events")
SG_DB=$(create_sg "${PREFIX}-sg-db" "RDS arena-events")
SG_EFS=$(create_sg "${PREFIX}-sg-efs" "EFS arena-events")

# Deixa a porta aberta só para o CIDR pedido: tira o que sobrou de execuções anteriores (ex.: 0.0.0.0/0).
sync_cidr() {
  local SG=$1 PORT=$2 WANT=$3 RULE CIDR FOUND=false
  while read -r RULE CIDR; do
    [ -z "${RULE}" ] && continue
    if [ "${CIDR}" = "${WANT}" ]; then FOUND=true
    else aws_q ec2 revoke-security-group-ingress --group-id "${SG}" --security-group-rule-ids "${RULE}" > /dev/null
    fi
  done < <(aws_q ec2 describe-security-group-rules --filters "Name=group-id,Values=${SG}" \
    --query "SecurityGroupRules[?!IsEgress && FromPort==\`${PORT}\` && CidrIpv4!=null].[SecurityGroupRuleId,CidrIpv4]" \
    --output text)
  if [ -n "${WANT}" ] && [ "${FOUND}" = false ]; then
    aws_q ec2 authorize-security-group-ingress --group-id "${SG}" --protocol tcp --port "${PORT}" \
      --cidr "${WANT}" > /dev/null
  fi
}
allow_sg() {
  local SG=$1 PORT=$2 SOURCE=$3 EXISTS
  EXISTS=$(aws_q ec2 describe-security-group-rules --filters "Name=group-id,Values=${SG}" \
    --query "SecurityGroupRules[?!IsEgress && FromPort==\`${PORT}\` && ReferencedGroupInfo.GroupId=='${SOURCE}'] | length(@)" \
    --output text)
  if [ "${EXISTS}" = "0" ]; then
    aws_q ec2 authorize-security-group-ingress --group-id "${SG}" --protocol tcp --port "${PORT}" \
      --source-group "${SOURCE}" > /dev/null
  fi
}
# Com o CloudFront, a porta 80 do ALB aceita só os endereços do CloudFront (lista gerenciada pela AWS): ninguém
# chega direto no endereço elb.amazonaws.com, e o WAF do CloudFront não é contornado.
CF_PREFIX_LIST=$(aws_q ec2 describe-managed-prefix-lists \
  --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
  --query 'PrefixLists[0].PrefixListId' --output text)
cf_rule() {  # regra da lista do CloudFront numa porta do ALB
  aws_q ec2 describe-security-group-rules --filters "Name=group-id,Values=${SG_ALB}" --output text \
    --query "SecurityGroupRules[?!IsEgress && FromPort==\`$1\` && PrefixListId=='${CF_PREFIX_LIST}'].SecurityGroupRuleId | [0]"
}
for PORT in 80 443; do
  RULE=$(cf_rule "${PORT}")
  if [ "${CLOUDFRONT}" = "1" ] && [ "${PORT}" = "${CF_PORT}" ]; then
    if none "${RULE}"; then
      aws_q ec2 authorize-security-group-ingress --group-id "${SG_ALB}" \
        --ip-permissions "IpProtocol=tcp,FromPort=${PORT},ToPort=${PORT},PrefixListIds=[{PrefixListId=${CF_PREFIX_LIST}}]" \
        > /dev/null
    fi
  elif ! none "${RULE}"; then
    aws_q ec2 revoke-security-group-ingress --group-id "${SG_ALB}" --security-group-rule-ids "${RULE}" > /dev/null
  fi
done
if [ "${CLOUDFRONT}" = "1" ]; then
  sync_cidr "${SG_ALB}" 80 ""
  sync_cidr "${SG_ALB}" 443 ""
else
  sync_cidr "${SG_ALB}" 80 "${ALLOWED_CIDR}"
  if [ "${USE_HTTPS}" = true ]; then sync_cidr "${SG_ALB}" 443 "${ALLOWED_CIDR}"; else sync_cidr "${SG_ALB}" 443 ""; fi
fi
# O security group do ALB é só deste script: regras de entrada em outras portas (ex.: criadas à mão no console
# durante testes) saem, porque podem abrir o ALB para mais do que o site.
while read -r RULE PORT; do
  [ -z "${RULE}" ] && continue
  echo "   Removendo regra avulsa do ALB (porta ${PORT}): ${RULE}"
  aws_q ec2 revoke-security-group-ingress --group-id "${SG_ALB}" --security-group-rule-ids "${RULE}" > /dev/null
done < <(aws_q ec2 describe-security-group-rules --filters "Name=group-id,Values=${SG_ALB}" \
  --query "SecurityGroupRules[?!IsEgress && !(FromPort==\`80\` && ToPort==\`80\`) && !(FromPort==\`443\` && ToPort==\`443\`)].[SecurityGroupRuleId,FromPort]" \
  --output text)
allow_sg "${SG_APP}" 8000 "${SG_ALB}"
allow_sg "${SG_DB}" 5432 "${SG_APP}"
allow_sg "${SG_EFS}" 2049 "${SG_APP}"

# ── 5. Segredos (SSM) ─────────────────────────────────────────────────────────
echo "[5/10] Segredos..."
get_param() {
  aws_q ssm get-parameter --name "$1" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || true
}
# Segredos passam por um arquivo temporário (só o seu usuário lê) e não pela linha de comando: assim não aparecem
# no histórico do shell nem na lista de processos.
SECRET_DIR=$(mktemp -d)
chmod 700 "${SECRET_DIR}"
cleanup() { rm -rf "${SECRET_DIR}" "${TASKDEF_DIR:-}"; }
trap cleanup EXIT
put_secret() {
  local FILE="${SECRET_DIR}/param.json"
  printf '%s' "$2" | python3 -c 'import json, sys
json.dump({"Name": sys.argv[1], "Value": sys.stdin.read(), "Type": "SecureString", "Overwrite": True},
          open(sys.argv[2], "w"))' "$1" "${FILE}"
  aws_q ssm put-parameter --cli-input-json "file://${FILE}" > /dev/null
  rm -f "${FILE}"
}
new_password() { python3 -c "import secrets; print(secrets.token_urlsafe(24))"; }

DB_ID="${PREFIX}-db"
DB_STATUS=$(aws_q rds describe-db-instances --db-instance-identifier "${DB_ID}" \
  --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null || true)

MASTER_PASSWORD=$(get_param "${SSM_PATH}/db_master_password")
if [ -z "${MASTER_PASSWORD}" ]; then
  if ! none "${DB_STATUS}"; then
    # Banco criado pela versão anterior deste script: a senha guardada em db_password é a do usuário mestre.
    MASTER_PASSWORD=$(get_param "${SSM_PATH}/db_password")
  fi
  [ -z "${MASTER_PASSWORD}" ] && MASTER_PASSWORD=$(new_password)
  put_secret "${SSM_PATH}/db_master_password" "${MASTER_PASSWORD}"
fi
APP_PASSWORD=$(get_param "${SSM_PATH}/db_password")
if [ -z "${APP_PASSWORD}" ] || [ "${APP_PASSWORD}" = "${MASTER_PASSWORD}" ]; then
  APP_PASSWORD=$(new_password)
  put_secret "${SSM_PATH}/db_password" "${APP_PASSWORD}"
fi
# Cabeçalho secreto que o CloudFront manda ao ALB (S-04): sem ele, o ALB responde 403. Assim, uma distribuição do
# CloudFront de outra pessoa apontada para o seu ALB não passa (a lista de endereços do CloudFront é de todos).
ORIGIN_SECRET=$(get_param "${SSM_PATH}/origin_secret")
if [ -z "${ORIGIN_SECRET}" ]; then
  ORIGIN_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  put_secret "${SSM_PATH}/origin_secret" "${ORIGIN_SECRET}"
fi
printf '%s' "${ORIGIN_SECRET}" > "${SECRET_DIR}/origin_secret"
unset ORIGIN_SECRET
if [ -z "$(get_param "${SSM_PATH}/secret_key")" ]; then
  put_secret "${SSM_PATH}/secret_key" "$(python3 -c \
    "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(64)))")"
fi
# Chave própria da senha do SMTP e dos segredos da verificação em duas etapas (core/crypto.py). Numa instalação que
# já existia, os segredos antigos continuam legíveis pela SECRET_KEY e a faxina diária os regrava com esta.
if [ -z "$(get_param "${SSM_PATH}/field_encryption_key")" ]; then
  put_secret "${SSM_PATH}/field_encryption_key" "$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")"
fi
unset APP_PASSWORD
# Endereço do /admin/ do Django: um caminho difícil de adivinhar, gerado uma vez e guardado no SSM.
ADMIN_URL=$(aws_q ssm get-parameter --name "${SSM_PATH}/admin_url" --query 'Parameter.Value' --output text \
  2>/dev/null || true)
if [ -z "${ADMIN_URL}" ]; then
  ADMIN_URL="gestao-$(python3 -c 'import secrets; print(secrets.token_hex(6))')/"
  aws_q ssm put-parameter --name "${SSM_PATH}/admin_url" --value "${ADMIN_URL}" --type String > /dev/null
fi

# ── 6. RDS PostgreSQL ─────────────────────────────────────────────────────────
echo "[6/10] RDS PostgreSQL (na primeira vez demora uns 5 a 10 minutos)..."
if ! aws_q rds describe-db-subnet-groups --db-subnet-group-name "${PREFIX}-db-sng" > /dev/null 2>&1; then
  aws_q rds create-db-subnet-group --db-subnet-group-name "${PREFIX}-db-sng" \
    --db-subnet-group-description "arena-events test" \
    --subnet-ids "${SUBNET_PRIV_A}" "${SUBNET_PRIV_B}" > /dev/null
fi
if none "${DB_STATUS}"; then
  # A versão mais nova do PostgreSQL 17 disponível na região (a mesma série do docker-compose).
  PG_VERSION=$(aws_q rds describe-db-engine-versions --engine postgres \
    --query "DBEngineVersions[?starts_with(EngineVersion, '17.')].EngineVersion | [-1]" --output text)
  DB_FILE="${SECRET_DIR}/db.json"
  printf '%s' "${MASTER_PASSWORD}" | python3 -c 'import json, sys
a = sys.argv
json.dump({
    "DBInstanceIdentifier": a[1], "DBInstanceClass": a[2], "Engine": "postgres", "EngineVersion": a[3],
    "MasterUsername": a[4], "MasterUserPassword": sys.stdin.read(), "DBName": a[5], "AllocatedStorage": 20,
    "StorageType": "gp3", "StorageEncrypted": True, "PubliclyAccessible": False, "VpcSecurityGroupIds": [a[6]],
    "DBSubnetGroupName": a[7], "BackupRetentionPeriod": int(a[9]), "MultiAZ": False,
    "DeletionProtection": a[10] == "1",
}, open(a[8], "w"))' "${DB_ID}" "${DB_CLASS}" "${PG_VERSION}" "${DB_MASTER_DEFAULT}" "${DB_NAME}" "${SG_DB}" \
    "${PREFIX}-db-sng" "${DB_FILE}" "${DB_BACKUP_DAYS}" "${DB_DELETION_PROTECTION}"
  aws_q rds create-db-instance --cli-input-json "file://${DB_FILE}" > /dev/null
  rm -f "${DB_FILE}"
  echo "   Aguardando o RDS (PostgreSQL ${PG_VERSION})..."
fi
unset MASTER_PASSWORD
aws_q rds wait db-instance-available --db-instance-identifier "${DB_ID}"
read -r DB_HOST DB_MASTER_USER DB_ENCRYPTED DB_BACKUPS DB_PROTECTED < <(aws_q rds describe-db-instances \
  --db-instance-identifier "${DB_ID}" --output text \
  --query 'DBInstances[0].[Endpoint.Address,MasterUsername,StorageEncrypted,BackupRetentionPeriod,DeletionProtection]')
# Backup e proteção contra exclusão acompanham as variáveis também num banco que já existia.
WANT_PROTECTED=False; [ "${DB_DELETION_PROTECTION}" = "1" ] && WANT_PROTECTED=True
if [ "${DB_BACKUPS}" != "${DB_BACKUP_DAYS}" ] || [ "${DB_PROTECTED}" != "${WANT_PROTECTED}" ]; then
  PROTECTION_FLAG="--no-deletion-protection"; [ "${WANT_PROTECTED}" = True ] && PROTECTION_FLAG="--deletion-protection"
  aws_q rds modify-db-instance --db-instance-identifier "${DB_ID}" --backup-retention-period "${DB_BACKUP_DAYS}" \
    "${PROTECTION_FLAG}" --apply-immediately > /dev/null
  echo "   Backup do banco: ${DB_BACKUP_DAYS} dia(s); proteção contra exclusão: ${WANT_PROTECTED}."
  aws_q rds wait db-instance-available --db-instance-identifier "${DB_ID}"
fi
if [ "${DB_ENCRYPTED}" != "True" ]; then
  echo "   ATENÇÃO: este banco foi criado sem criptografia em disco, e isso não muda depois de criado."
  echo "   Para um banco criptografado: ./infra/teardown.sh e rode este script de novo (os dados são apagados)."
fi
echo "   RDS: ${DB_HOST} (mestre: ${DB_MASTER_USER}, aplicação: ${DB_APP_USER})"

# ── 7. EFS (arquivos enviados: capas, assinaturas, materiais, trabalhos) ──────
echo "[7/10] EFS..."
EFS_ID=$(aws_q efs describe-file-systems --creation-token "${PREFIX}-files" \
  --query 'FileSystems[0].FileSystemId' --output text 2>/dev/null || true)
if none "${EFS_ID}"; then
  EFS_ID=$(aws_q efs create-file-system --creation-token "${PREFIX}-files" --encrypted \
    --performance-mode generalPurpose --throughput-mode bursting \
    --tags "Key=Name,Value=${PREFIX}-files" --query 'FileSystemId' --output text)
fi
until [ "$(aws_q efs describe-file-systems --file-system-id "${EFS_ID}" \
  --query 'FileSystems[0].LifeCycleState' --output text)" = "available" ]; do sleep 5; done
aws_q efs put-backup-policy --file-system-id "${EFS_ID}" --backup-policy Status=ENABLED > /dev/null
for SUBNET in "${SUBNET_PRIV_A}" "${SUBNET_PRIV_B}"; do
  MT=$(aws_q efs describe-mount-targets --file-system-id "${EFS_ID}" \
    --query "MountTargets[?SubnetId=='${SUBNET}'].MountTargetId | [0]" --output text)
  if none "${MT}"; then
    aws_q efs create-mount-target --file-system-id "${EFS_ID}" --subnet-id "${SUBNET}" \
      --security-groups "${SG_EFS}" > /dev/null
  fi
done
access_point() {
  local NAME=$1 ID
  ID=$(aws_q efs describe-access-points --file-system-id "${EFS_ID}" \
    --query "AccessPoints[?Name=='${NAME}'].AccessPointId | [0]" --output text)
  if none "${ID}"; then
    ID=$(aws_q efs create-access-point --file-system-id "${EFS_ID}" \
      --posix-user "Uid=${APP_UID},Gid=${APP_UID}" \
      --root-directory "Path=/${NAME},CreationInfo={OwnerUid=${APP_UID},OwnerGid=${APP_UID},Permissions=750}" \
      --tags "Key=Name,Value=${NAME}" --query 'AccessPointId' --output text)
  fi
  echo "${ID}"
}
AP_MEDIA=$(access_point media)
AP_PRIVATE=$(access_point private_media)
until [ "$(aws_q efs describe-mount-targets --file-system-id "${EFS_ID}" \
  --query "length(MountTargets[?LifeCycleState!='available'])" --output text)" = "0" ]; do sleep 5; done
echo "   EFS=${EFS_ID}"

# ── 8. ALB (antes da task definition: o endereço dele vai para ALLOWED_HOSTS e SITE_URL) ──
echo "[8/10] Application Load Balancer..."
ALB_ARN=$(aws_q elbv2 describe-load-balancers --names "${PREFIX}-alb" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
if none "${ALB_ARN}"; then
  ALB_ARN=$(aws_q elbv2 create-load-balancer --name "${PREFIX}-alb" \
    --subnets "${SUBNET_PUB_A}" "${SUBNET_PUB_B}" --security-groups "${SG_ALB}" \
    --scheme internet-facing --type application \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
fi
# Registros de acesso (S-09): bucket do ambiente, privado, criptografado e com expiração em LOG_DAYS dias.
LOG_BUCKET="${PREFIX}-logs-${AWS_ACCOUNT_ID}"
if [ "${ACCESS_LOGS}" = "1" ]; then
  if ! aws_q s3api head-bucket --bucket "${LOG_BUCKET}" > /dev/null 2>&1; then
    if [ "${REGION}" = "us-east-1" ]; then
      aws_q s3api create-bucket --bucket "${LOG_BUCKET}" > /dev/null
    else
      aws_q s3api create-bucket --bucket "${LOG_BUCKET}" \
        --create-bucket-configuration "LocationConstraint=${REGION}" > /dev/null
    fi
  fi
  aws_q s3api put-public-access-block --bucket "${LOG_BUCKET}" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true > /dev/null
  # O CloudFront grava os registros com permissão por ACL (só para a conta de entrega de logs da AWS).
  aws_q s3api put-bucket-ownership-controls --bucket "${LOG_BUCKET}" \
    --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerPreferred}]' > /dev/null
  aws_q s3api put-bucket-encryption --bucket "${LOG_BUCKET}" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' > /dev/null
  LOG_FILE="$(mktemp)"
  python3 - "${LOG_FILE}" "${LOG_DAYS}" <<'PY'
import json, sys

rule = {'ID': 'expira', 'Status': 'Enabled', 'Filter': {'Prefix': ''}, 'Expiration': {'Days': int(sys.argv[2])}}
json.dump({'Rules': [rule]}, open(sys.argv[1], 'w'))
PY
  aws_q s3api put-bucket-lifecycle-configuration --bucket "${LOG_BUCKET}" \
    --lifecycle-configuration "file://${LOG_FILE}" > /dev/null
  # Quem grava os registros do ALB: o serviço de entrega e, nas regiões mais antigas, a conta do ELB da região.
  python3 - "${LOG_FILE}" "${LOG_BUCKET}" "${AWS_ACCOUNT_ID}" "${REGION}" <<'PY'
import json, sys

path, bucket, account, region = sys.argv[1:5]
elb_accounts = {
    'us-east-1': '127311923021', 'us-east-2': '033677994240', 'us-west-1': '027434742980',
    'us-west-2': '797873946194', 'sa-east-1': '507241528517', 'eu-west-1': '156460612806',
    'eu-central-1': '054676820928',
}
principals = [{'Service': 'logdelivery.elasticloadbalancing.amazonaws.com'}]
if region in elb_accounts:
    principals.append({'AWS': f'arn:aws:iam::{elb_accounts[region]}:root'})
statements = [
    {'Effect': 'Allow', 'Principal': principal, 'Action': 's3:PutObject',
     'Resource': f'arn:aws:s3:::{bucket}/alb/AWSLogs/{account}/*'}
    for principal in principals
]
statements.append({
    'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
    'Resource': [f'arn:aws:s3:::{bucket}', f'arn:aws:s3:::{bucket}/*'],
    'Condition': {'Bool': {'aws:SecureTransport': 'false'}},
})
json.dump({'Version': '2012-10-17', 'Statement': statements}, open(path, 'w'))
PY
  aws_q s3api put-bucket-policy --bucket "${LOG_BUCKET}" --policy "file://${LOG_FILE}" > /dev/null
  rm -f "${LOG_FILE}"
  ALB_LOG_ATTRS="Key=access_logs.s3.enabled,Value=true Key=access_logs.s3.bucket,Value=${LOG_BUCKET} Key=access_logs.s3.prefix,Value=alb"
else
  ALB_LOG_ATTRS="Key=access_logs.s3.enabled,Value=false"
fi
# PDFs e relatórios grandes podem passar de 60 s (o gunicorn espera até 120 s).
# shellcheck disable=SC2086
aws_q elbv2 modify-load-balancer-attributes --load-balancer-arn "${ALB_ARN}" \
  --attributes Key=idle_timeout.timeout_seconds,Value=120 ${ALB_LOG_ATTRS} > /dev/null
ALB_DNS=$(aws_q elbv2 describe-load-balancers --load-balancer-arns "${ALB_ARN}" \
  --query 'LoadBalancers[0].DNSName' --output text)

TG_ARN=$(aws_q elbv2 describe-target-groups --names "${PREFIX}-tg" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if none "${TG_ARN}"; then
  TG_ARN=$(aws_q elbv2 create-target-group --name "${PREFIX}-tg" \
    --protocol HTTP --port 8000 --vpc-id "${VPC_ID}" --target-type ip \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
fi
# /saude/ responde sem conferir o host (o ALB chama pelo IP da tarefa) e testa o banco.
aws_q elbv2 modify-target-group --target-group-arn "${TG_ARN}" \
  --health-check-path /saude/ --matcher HttpCode=200 --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 3 > /dev/null
aws_q elbv2 modify-target-group-attributes --target-group-arn "${TG_ARN}" \
  --attributes Key=deregistration_delay.timeout_seconds,Value=30 > /dev/null

listener_arn() {
  aws_q elbv2 describe-listeners --load-balancer-arn "${ALB_ARN}" \
    --query "Listeners[?Port==\`$1\`].ListenerArn | [0]" --output text
}
FORWARD="Type=forward,TargetGroupArn=${TG_ARN}"
REDIRECT='Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}'
DENY='Type=fixed-response,FixedResponseConfig={StatusCode=403,ContentType=text/plain,MessageBody=Acesso direto nao permitido}'
GUARD_PRIORITY=10
guard_rule() {  # regra que só encaminha pedidos com o cabeçalho secreto (vazio se não houver)
  none "$1" && return 0
  aws_q elbv2 describe-rules --listener-arn "$1" --output text \
    --query "Rules[?Priority=='${GUARD_PRIORITY}'].RuleArn | [0]"
}
L80=$(listener_arn 80)
L443=$(listener_arn 443)
# No CloudFront, o ALB só encaminha com o cabeçalho secreto: o padrão do listener é 403. Na primeira vez, o padrão
# continua "encaminhar" até o CloudFront estar publicado com o cabeçalho (fim do script), para o site não cair.
GUARD_LISTENER=""
[ "${CLOUDFRONT}" = "1" ] && GUARD_LISTENER=$([ "${CF_PORT}" = 443 ] && echo "${L443}" || echo "${L80}")
GUARD_EXISTS=0
if [ "${CLOUDFRONT}" = "1" ] && ! none "$(guard_rule "${GUARD_LISTENER}")"; then GUARD_EXISTS=1; fi
SITE_ACTION="${FORWARD}"
[ "${GUARD_EXISTS}" = 1 ] && SITE_ACTION="${DENY}"
if [ "${USE_HTTPS}" = true ]; then
  if none "${L443}"; then
    aws_q elbv2 create-listener --load-balancer-arn "${ALB_ARN}" --protocol HTTPS --port 443 \
      --certificates "CertificateArn=${CERT_ARN}" --ssl-policy ELBSecurityPolicy-TLS13-1-2-2021-06 \
      --default-actions "${SITE_ACTION}" > /dev/null
  else
    aws_q elbv2 modify-listener --listener-arn "${L443}" --certificates "CertificateArn=${CERT_ARN}" \
      --default-actions "${SITE_ACTION}" > /dev/null
  fi
  ACTION_80="${REDIRECT}"
  [ "${CLOUDFRONT}" = "1" ] && ACTION_80="${DENY}"  # o CloudFront chega pela 443; a 80 nem fica aberta
else
  if ! none "${L443}"; then aws_q elbv2 delete-listener --listener-arn "${L443}"; fi
  ACTION_80="${SITE_ACTION}"
fi
if none "${L80}"; then
  aws_q elbv2 create-listener --load-balancer-arn "${ALB_ARN}" --protocol HTTP --port 80 \
    --default-actions "${ACTION_80}" > /dev/null
else
  aws_q elbv2 modify-listener --listener-arn "${L80}" --default-actions "${ACTION_80}" > /dev/null
fi
L80=$(listener_arn 80)
L443=$(listener_arn 443)
if [ "${CLOUDFRONT}" = "1" ]; then
  GUARD_LISTENER=$([ "${CF_PORT}" = 443 ] && echo "${L443}" || echo "${L80}")
fi
# Sem CloudFront, a regra do cabeçalho não tem razão de existir (o acesso é direto, limitado por IP).
for LISTENER in "${L80}" "${L443}"; do
  RULE=$(guard_rule "${LISTENER}")
  if ! none "${RULE}" && { [ "${CLOUDFRONT}" != "1" ] || [ "${LISTENER}" != "${GUARD_LISTENER}" ]; }; then
    aws_q elbv2 delete-rule --rule-arn "${RULE}"
  fi
done

# ── WAF (WAF=1) e CloudFront (CLOUDFRONT=1) ───────────────────────────────────
# Sem CloudFront, o WAF fica no ALB (escopo REGIONAL). Com CloudFront, fica no CloudFront (escopo CLOUDFRONT,
# sempre em us-east-1): só ali o WAF vê o IP de quem acessa (no ALB, todos chegariam com o IP do CloudFront).
cfq() { aws "$@" --region us-east-1; }
waf_q() {  # escopo, depois o comando wafv2
  local SCOPE=$1
  shift
  if [ "${SCOPE}" = CLOUDFRONT ]; then cfq wafv2 "$@"; else aws_q wafv2 "$@"; fi
}
waf_id() { waf_q "$1" list-web-acls --scope "$1" --query "WebACLs[?Name=='$2'].Id | [0]" --output text; }
waf_upsert() {  # escopo, nome: cria ou atualiza as regras e devolve o ARN
  local SCOPE=$1 NAME=$2 ID LOCK FILE="${SECRET_DIR}/waf-$1.json"
  python3 - "${FILE}" "${NAME}" "${WAF_RATE_LIMIT}" "${SCOPE}" <<'PY'
import json, sys

path, name, limit, scope = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]


def visibility(metric):
    return {'SampledRequestsEnabled': True, 'CloudWatchMetricsEnabled': True, 'MetricName': metric}


def managed(priority, rule_set, overrides=()):
    statement = {'VendorName': 'AWS', 'Name': rule_set}
    if overrides:
        # Regras em modo "contar": não bloqueiam (ex.: o limite de 8 KB no corpo barraria os uploads de arquivos).
        statement['RuleActionOverrides'] = [{'Name': r, 'ActionToUse': {'Count': {}}} for r in overrides]
    return {
        'Name': rule_set,
        'Priority': priority,
        'Statement': {'ManagedRuleGroupStatement': statement},
        'OverrideAction': {'None': {}},
        'VisibilityConfig': visibility(rule_set),
    }


rules = [
    {
        'Name': 'limite-por-ip',
        'Priority': 0,
        'Statement': {'RateBasedStatement': {'Limit': limit, 'AggregateKeyType': 'IP'}},
        'Action': {'Block': {}},
        'VisibilityConfig': visibility('limite-por-ip'),
    },
    managed(1, 'AWSManagedRulesAmazonIpReputationList'),
    managed(2, 'AWSManagedRulesKnownBadInputsRuleSet'),
    managed(3, 'AWSManagedRulesCommonRuleSet', ['SizeRestrictions_BODY', 'CrossSiteScripting_BODY']),
]
json.dump(
    {
        'Name': name,
        'Scope': scope,
        'DefaultAction': {'Allow': {}},
        'Rules': rules,
        'VisibilityConfig': visibility(name),
    },
    open(path, 'w'),
)
PY
  ID=$(waf_id "${SCOPE}" "${NAME}" 2>/dev/null || true)
  if none "${ID}"; then
    waf_q "${SCOPE}" create-web-acl --cli-input-json "file://${FILE}" --query 'Summary.ARN' --output text
  else
    LOCK=$(waf_q "${SCOPE}" get-web-acl --scope "${SCOPE}" --name "${NAME}" --id "${ID}" --query 'LockToken' \
      --output text)
    waf_q "${SCOPE}" update-web-acl --cli-input-json "file://${FILE}" --id "${ID}" --lock-token "${LOCK}" > /dev/null
    waf_q "${SCOPE}" get-web-acl --scope "${SCOPE}" --name "${NAME}" --id "${ID}" --query 'WebACL.ARN' --output text
  fi
}
waf_delete() {  # escopo, nome (já desassociado de tudo)
  local SCOPE=$1 NAME=$2 ID LOCK
  ID=$(waf_id "${SCOPE}" "${NAME}" 2>/dev/null || true)
  none "${ID}" && return 0
  LOCK=$(waf_q "${SCOPE}" get-web-acl --scope "${SCOPE}" --name "${NAME}" --id "${ID}" --query 'LockToken' \
    --output text)
  if ! waf_q "${SCOPE}" delete-web-acl --scope "${SCOPE}" --name "${NAME}" --id "${ID}" --lock-token "${LOCK}" \
    2> /dev/null; then
    echo "   O WAF ${NAME} ainda está sendo desligado; rode o setup de novo daqui a alguns minutos para apagá-lo."
  fi
}

WAF_ALB="${PREFIX}-waf"
WAF_CF="${PREFIX}-waf-cf"
if [ "${WAF}" = "1" ] && [ "${CLOUDFRONT}" != "1" ]; then
  echo "   WAF no ALB (limite de ${WAF_RATE_LIMIT} requisições por IP em 5 minutos)..."
  WAF_ARN=$(waf_upsert REGIONAL "${WAF_ALB}")
  CURRENT=$(aws_q wafv2 get-web-acl-for-resource --resource-arn "${ALB_ARN}" --query 'WebACL.ARN' --output text \
    2>/dev/null || true)
  if [ "${CURRENT}" != "${WAF_ARN}" ]; then
    # Logo depois de criada, a regra pode levar alguns segundos para ficar disponível na região.
    for TRY in 1 2 3 4 5 6; do
      aws_q wafv2 associate-web-acl --web-acl-arn "${WAF_ARN}" --resource-arn "${ALB_ARN}" 2> /dev/null && break
      [ "${TRY}" = 6 ] && { echo "   Não consegui ligar o WAF ao ALB; rode o setup de novo." >&2; exit 1; }
      sleep "${RETRY_SLEEP:-10}"
    done
  fi
elif ! none "$(waf_id REGIONAL "${WAF_ALB}" 2>/dev/null || true)"; then
  echo "   Tirando o WAF do ALB..."
  aws_q wafv2 disassociate-web-acl --resource-arn "${ALB_ARN}" 2> /dev/null || true
  waf_delete REGIONAL "${WAF_ALB}"
fi

CF_ID=$(cfq cloudfront list-distributions --query "DistributionList.Items[?Comment=='${PREFIX}'].Id | [0]" \
  --output text 2>/dev/null || true)
CF_DOMAIN=""
if [ "${CLOUDFRONT}" = "1" ]; then
  CF_WAF_ARN=""
  if [ "${WAF}" = "1" ]; then
    echo "   WAF no CloudFront (limite de ${WAF_RATE_LIMIT} requisições por IP em 5 minutos)..."
    CF_WAF_ARN=$(waf_upsert CLOUDFRONT "${WAF_CF}")
  fi
  policy() {  # tipo (cache ou origin-request), nome da política gerenciada pela AWS
    if [ "$1" = cache ]; then
      cfq cloudfront list-cache-policies --type managed --output text \
        --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='$2'].CachePolicy.Id | [0]"
    else
      cfq cloudfront list-origin-request-policies --type managed --output text \
        --query "OriginRequestPolicyList.Items[?OriginRequestPolicy.OriginRequestPolicyConfig.Name=='$2'].OriginRequestPolicy.Id | [0]"
    fi
  }
  NO_CACHE=$(policy cache Managed-CachingDisabled)
  STATIC_CACHE=$(policy cache Managed-CachingOptimized)
  # Passa ao site tudo o que o navegador mandou (host, cookies, formulários) e o CloudFront-Forwarded-Proto.
  ALL_HEADERS=$(policy origin-request Managed-AllViewerAndCloudFrontHeaders-2022-06)
  CF_FILE="${SECRET_DIR}/cloudfront.json"
  CF_CURRENT="${SECRET_DIR}/cloudfront-atual.json"  # configuração atual (vazio: distribuição nova)
  : > "${CF_CURRENT}"
  CF_ETAG=""
  CF_CREATED=0
  if ! none "${CF_ID}"; then
    # Configuração e ETag na mesma leitura: a atualização parte do que existe (a AWS exige todos os campos,
    # como Aliases e Restrictions) e troca só o que o script controla.
    cfq cloudfront get-distribution-config --id "${CF_ID}" --output json > "${CF_CURRENT}"
    CF_ETAG=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["ETag"])' "${CF_CURRENT}")
  fi
  CF_DOMAIN_ARG=""
[ "${USE_HTTPS}" = true ] && CF_DOMAIN_ARG="${DOMAIN}"
  python3 - "${CF_FILE}" "${PREFIX}-$(date +%s)" "${PREFIX}" "${ALB_DNS}" "${CF_WAF_ARN}" "${NO_CACHE}" \
    "${STATIC_CACHE}" "${ALL_HEADERS}" "${CF_CURRENT}" "${CF_ORIGIN_TIMEOUT}" "${SECRET_DIR}/origin_secret" \
    "${ORIGIN_HEADER}" "${CF_DOMAIN_ARG}" "${CERT_ARN}" "${ACCESS_LOGS}" "${LOG_BUCKET}" <<'PY'
import json, os, sys

path, ref, comment, origin, waf, no_cache, static_cache, all_headers, current, timeout = sys.argv[1:11]
secret_file, header_name, domain, cert, access_logs, log_bucket = sys.argv[11:17]
secret = open(secret_file).read().strip()
methods = ['GET', 'HEAD', 'OPTIONS', 'PUT', 'POST', 'PATCH', 'DELETE']
cached = {'Quantity': 2, 'Items': ['GET', 'HEAD']}
config = {
    'CallerReference': ref,
    'Comment': comment,
    'Enabled': True,
    'Origins': {
        'Quantity': 1,
        'Items': [
            {
                'Id': 'alb',
                'DomainName': origin,
                # CloudFront -> ALB: HTTPS com domínio (o certificado do ALB vale para o domínio, que o CloudFront
                # repassa no Host); sem domínio, HTTP. O ALB só aceita o seu CloudFront, pelo cabeçalho secreto.
                'CustomHeaders': {'Quantity': 1, 'Items': [{'HeaderName': header_name, 'HeaderValue': secret}]},
                'CustomOriginConfig': {
                    'HTTPPort': 80,
                    'HTTPSPort': 443,
                    'OriginProtocolPolicy': 'https-only' if domain else 'http-only',
                    'OriginReadTimeout': max(1, min(180, int(timeout))),
                    'OriginKeepaliveTimeout': 5,
                    'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']},
                },
            }
        ],
    },
    'DefaultCacheBehavior': {
        'TargetOriginId': 'alb',
        'ViewerProtocolPolicy': 'redirect-to-https',
        'AllowedMethods': {'Quantity': len(methods), 'Items': methods, 'CachedMethods': cached},
        'Compress': True,
        'CachePolicyId': no_cache,  # páginas do site: nunca em cache (cada pessoa vê as suas)
        'OriginRequestPolicyId': all_headers,
    },
    'CacheBehaviors': {
        'Quantity': 1,
        'Items': [
            {
                'PathPattern': '/static/*',  # CSS, JS e imagens do sistema: em cache nas bordas
                'TargetOriginId': 'alb',
                'ViewerProtocolPolicy': 'redirect-to-https',
                'AllowedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD'], 'CachedMethods': cached},
                'Compress': True,
                'CachePolicyId': static_cache,
                # Mesmos cabeçalhos das páginas (host e CloudFront-Forwarded-Proto): sem eles, o Django veria um
                # pedido HTTP com o host do ALB e responderia com redirecionamento ou erro, e o site ficaria sem
                # CSS e JS. Ficam fora da chave do cache: o arquivo continua em cache para todos.
                'OriginRequestPolicyId': all_headers,
            }
        ],
    },
    'PriceClass': 'PriceClass_All',  # inclui as bordas da América do Sul
    'HttpVersion': 'http2and3',
    'IsIPV6Enabled': True,
    'WebACLId': waf,
}
aliases = {'Quantity': 1, 'Items': [domain]} if domain else {'Quantity': 0}
viewer_certificate = (
    {'ACMCertificateArn': cert, 'SSLSupportMethod': 'sni-only', 'MinimumProtocolVersion': 'TLSv1.2_2021'}
    if domain
    # Com o certificado padrão (*.cloudfront.net), a AWS fixa a versão mínima em TLSv1, mas exige o campo
    # na atualização.
    else {'CloudFrontDefaultCertificate': True, 'MinimumProtocolVersion': 'TLSv1'}
)


def merge(old, new):
    """Põe os campos do script por cima da configuração atual, item por item (origens pelo Id, comportamentos
    pelo PathPattern): na atualização a AWS exige todos os campos, inclusive os que o script não usa."""
    if isinstance(old, dict) and isinstance(new, dict):
        return {**old, **{key: merge(old.get(key), value) for key, value in new.items()}}
    if isinstance(old, list) and isinstance(new, list):
        def ident(item):
            return item.get('Id') or item.get('PathPattern') if isinstance(item, dict) else None

        current = {ident(item): item for item in old if ident(item)}
        return [merge(current.get(ident(item)), item) for item in new]
    return new


if os.path.getsize(current):
    existing = json.load(open(current))['DistributionConfig']
    config['CallerReference'] = existing['CallerReference']
    config = merge(existing, config)
# Estes dois são trocados inteiros (misturar o certificado padrão com o do domínio é inválido). Sem domínio e já
# no certificado padrão, fica o que a AWS devolveu (com todos os campos que ela espera de volta).
config['Aliases'] = aliases
if domain or not config.get('ViewerCertificate', {}).get('CloudFrontDefaultCertificate'):
    config['ViewerCertificate'] = viewer_certificate
if access_logs == '1':
    config['Logging'] = {
        'Enabled': True, 'IncludeCookies': False, 'Bucket': f'{log_bucket}.s3.amazonaws.com', 'Prefix': 'cloudfront/'
    }
else:
    config['Logging'] = {'Enabled': False, 'IncludeCookies': False, 'Bucket': '', 'Prefix': ''}
json.dump(config, open(path, 'w'))
PY
  if none "${CF_ID}"; then
    echo "   Criando o CloudFront (fica pronto em 5 a 15 minutos)..."
    CF_ID=$(cfq cloudfront create-distribution --distribution-config "file://${CF_FILE}" \
      --query 'Distribution.Id' --output text)
    CF_CREATED=1
  else
    echo "   Atualizando o CloudFront (o site continua no ar enquanto a mudança se espalha)..."
    cfq cloudfront update-distribution --id "${CF_ID}" --if-match "${CF_ETAG}" \
      --distribution-config "file://${CF_FILE}" > /dev/null
    # Cada deploy pode trazer CSS e JS novos: o CloudFront esquece o que guardou de /static/.
    cfq cloudfront create-invalidation --distribution-id "${CF_ID}" --paths '/static/*' > /dev/null
  fi
  CF_DOMAIN=$(cfq cloudfront get-distribution --id "${CF_ID}" --query 'Distribution.DomainName' --output text)
  if [ "${WAF}" != "1" ]; then waf_delete CLOUDFRONT "${WAF_CF}"; fi
elif ! none "${CF_ID}"; then
  echo "   Aviso: a distribuição do CloudFront ${CF_ID} continua existindo, mas o ALB não aceita mais o CloudFront."
  echo "   Para apagá-la: STAGE=${STAGE} ./infra/teardown.sh, ou desative e apague pelo console."
fi

if [ "${CLOUDFRONT}" = "1" ]; then
  PUBLIC_HOST="${CF_DOMAIN}"
  [ "${USE_HTTPS}" = true ] && PUBLIC_HOST="${DOMAIN}"
  SITE_HTTPS=true
  SITE_URL="https://${PUBLIC_HOST}"
  ALLOWED_HOSTS="${PUBLIC_HOST}"
  PROXIES=2  # CloudFront e ALB: o IP de quem acessa é o penúltimo do X-Forwarded-For
elif [ "${USE_HTTPS}" = true ]; then
  PUBLIC_HOST="${DOMAIN}"
  SITE_HTTPS=true
  SITE_URL="https://${DOMAIN}"
  ALLOWED_HOSTS="${DOMAIN},${ALB_DNS}"
  PROXIES=1
else
  PUBLIC_HOST="${ALB_DNS}"
  SITE_HTTPS=false
  SITE_URL="http://${ALB_DNS}"
  ALLOWED_HOSTS="${ALB_DNS}"
  PROXIES=1
fi
echo "   ALB: ${ALB_DNS}"

# ── 9. IAM, logs e task definitions ───────────────────────────────────────────
echo "[9/10] ECS: papéis, logs e task definitions..."
ECS_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
ensure_role() {
  if ! aws iam get-role --role-name "$1" > /dev/null 2>&1; then
    aws iam create-role --role-name "$1" --assume-role-policy-document "${ECS_TRUST}" > /dev/null
  fi
}
# Papel de execução: o ECS baixa a imagem, lê os segredos do SSM e grava os logs.
EXEC_ROLE="${PREFIX}-ecs-exec-role"
ensure_role "${EXEC_ROLE}"
aws iam attach-role-policy --role-name "${EXEC_ROLE}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name "${EXEC_ROLE}" --policy-name ssm-read --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameters\",\"ssm:GetParameter\"],\"Resource\":\"arn:aws:ssm:${REGION}:${AWS_ACCOUNT_ID}:parameter${SSM_PATH}/*\"}]}"
# Papel da tarefa: o que a aplicação pode fazer na AWS (ECS Exec para comandos de manutenção e o EFS).
TASK_ROLE="${PREFIX}-ecs-task-role"
ensure_role "${TASK_ROLE}"
aws iam put-role-policy --role-name "${TASK_ROLE}" --policy-name exec-and-efs --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssmmessages:CreateControlChannel\",\"ssmmessages:CreateDataChannel\",\"ssmmessages:OpenControlChannel\",\"ssmmessages:OpenDataChannel\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"elasticfilesystem:ClientMount\",\"elasticfilesystem:ClientWrite\"],\"Resource\":\"arn:aws:elasticfilesystem:${REGION}:${AWS_ACCOUNT_ID}:file-system/${EFS_ID}\"}]}"
EXEC_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${EXEC_ROLE}"
TASK_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${TASK_ROLE}"

LOG_GROUP="/ecs/${PREFIX}"
if none "$(aws_q logs describe-log-groups --log-group-name-prefix "${LOG_GROUP}" \
  --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName | [0]" --output text)"; then
  aws_q logs create-log-group --log-group-name "${LOG_GROUP}"
fi
aws_q logs put-retention-policy --log-group-name "${LOG_GROUP}" --retention-in-days 14

# Papel que o próprio ECS usa para criar as interfaces de rede das tarefas. Contas novas, que nunca abriram o ECS
# pelo console, não têm: sem ele, o run-task falha com "Unable to assume the service linked role".
if ! aws iam get-role --role-name AWSServiceRoleForECS > /dev/null 2>&1; then
  aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com > /dev/null
  echo "   Papel do serviço ECS criado; aguardando a AWS propagar..."
  sleep 20
fi
if ! aws_q ecs describe-clusters --clusters "${PREFIX}" --query 'clusters[0].status' --output text \
  | grep -q ACTIVE; then
  aws_q ecs create-cluster --cluster-name "${PREFIX}" > /dev/null
fi
if [ -n "${ALERT_EMAIL}" ]; then
  # Container Insights dá o número de tarefas rodando por serviço, usado no alarme do worker (alguns dólares/mês).
  aws_q ecs update-cluster-settings --cluster "${PREFIX}" --settings name=containerInsights,value=enabled > /dev/null
fi

# As task definitions são montadas em Python (JSON sem problemas de aspas). Três famílias:
#   PREFIX          web (gunicorn), atrás do ALB; pode ter várias tarefas (WEB_COUNT)
#   PREFIX-worker   worker (fila de e-mails, lembretes, vagas da fila, faxina e ZIPs), serviço próprio com 1 tarefa
#   PREFIX-migrate  migrate + setup_db_roles, com o usuário mestre; roda e termina antes de cada deploy
TASKDEF_DIR=$(mktemp -d)
export FULL_IMAGE REGION AWS_ACCOUNT_ID SSM_PATH PREFIX DB_NAME DB_APP_USER DB_MASTER_USER DB_HOST \
  SITE_URL ALLOWED_HOSTS SITE_HTTPS PUBLIC_HOST PROXIES CLOUDFRONT EFS_ID AP_MEDIA AP_PRIVATE EXEC_ROLE_ARN TASK_ROLE_ARN LOG_GROUP TASKDEF_DIR DOMAIN \
  ADMIN_URL HSTS_SECONDS
python3 - <<'PY'
import json, os

e = os.environ
ssm = f"arn:aws:ssm:{e['REGION']}:{e['AWS_ACCOUNT_ID']}:parameter{e['SSM_PATH']}"
https = e['SITE_HTTPS'] == 'true'
base_env = {
    'DEBUG': 'False',
    'DB_NAME': e['DB_NAME'],
    'DB_USER': e['DB_APP_USER'],
    'DB_HOST': e['DB_HOST'],
    'DB_PORT': '5432',
    'DB_SSLMODE': 'verify-full',
    'PGSSLROOTCERT': '/app/certs/rds-global-bundle.pem',
    'ALLOWED_HOSTS': e['ALLOWED_HOSTS'],
    'SITE_URL': e['SITE_URL'],
    'HTTPS_ENABLED': 'True' if https else 'False',
    'NUM_PROXIES': e['PROXIES'],
    'SERVE_MEDIA': 'True',
    'LOG_LEVEL': 'INFO',
    'ADMIN_URL': e['ADMIN_URL'],
}
if e['CLOUDFRONT'] == '1':
    base_env['PROXY_SSL_HEADER'] = 'CloudFront-Forwarded-Proto'  # o ALB recebe HTTP do CloudFront
if https:
    base_env['CSRF_TRUSTED_ORIGINS'] = f"https://{e['PUBLIC_HOST']}"
    base_env['SECURE_HSTS_SECONDS'] = e['HSTS_SECONDS']
secrets = [
    {'name': 'SECRET_KEY', 'valueFrom': f'{ssm}/secret_key'},
    {'name': 'FIELD_ENCRYPTION_KEY', 'valueFrom': f'{ssm}/field_encryption_key'},
    {'name': 'DB_PASSWORD', 'valueFrom': f'{ssm}/db_password'},
]


def logs(stream):
    return {
        'logDriver': 'awslogs',
        'options': {'awslogs-group': e['LOG_GROUP'], 'awslogs-region': e['REGION'], 'awslogs-stream-prefix': stream},
    }


def env(extra=None):
    return [{'name': k, 'value': v} for k, v in {**base_env, **(extra or {})}.items()]


mounts = [
    {'sourceVolume': 'media', 'containerPath': '/app/media'},
    {'sourceVolume': 'private_media', 'containerPath': '/app/private_media'},
]
volumes = [
    {
        'name': name,
        'efsVolumeConfiguration': {
            'fileSystemId': e['EFS_ID'],
            'transitEncryption': 'ENABLED',
            'authorizationConfig': {'accessPointId': ap, 'iam': 'ENABLED'},
        },
    }
    for name, ap in (('media', e['AP_MEDIA']), ('private_media', e['AP_PRIVATE']))
]
common = {
    'networkMode': 'awsvpc',
    'requiresCompatibilities': ['FARGATE'],
    'runtimePlatform': {'cpuArchitecture': 'X86_64', 'operatingSystemFamily': 'LINUX'},
    'executionRoleArn': e['EXEC_ROLE_ARN'],
    'taskRoleArn': e['TASK_ROLE_ARN'],
}
gunicorn = (
    'gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile -'
)
app = {
    **common,
    'family': e['PREFIX'],
    'cpu': '512',
    'memory': '1024',
    'volumes': volumes,
    'containerDefinitions': [
        {
            'name': 'web',
            'image': e['FULL_IMAGE'],
            'essential': True,
            'command': ['sh', '-c', gunicorn],
            'portMappings': [{'containerPort': 8000}],
            # Quem envia os e-mails é o worker: um e-mail na fila não se perde se o web reiniciar.
            'environment': env({'OUTBOX_MODE': 'off'}),
            'secrets': secrets,
            'mountPoints': mounts,
            'logConfiguration': logs('web'),
        },
    ],
}
worker = {
    **common,
    'family': f"{e['PREFIX']}-worker",
    'cpu': '256',
    'memory': '512',
    'volumes': volumes,
    'containerDefinitions': [
        {
            'name': 'worker',
            'image': e['FULL_IMAGE'],
            'essential': True,
            'command': ['python', 'manage.py', 'process_outbox', '--continuo'],
            'environment': env(),
            'secrets': secrets,
            'mountPoints': mounts,
            'logConfiguration': logs('worker'),
        },
    ],
}
migrate = {
    **common,
    'family': f"{e['PREFIX']}-migrate",
    'cpu': '256',
    'memory': '512',
    'containerDefinitions': [
        {
            'name': 'migrate',
            'image': e['FULL_IMAGE'],
            'essential': True,
            'command': ['sh', '-c', 'python manage.py migrate --noinput && python manage.py setup_db_roles'],
            'environment': env({'DB_MIGRATE_USER': e['DB_MASTER_USER']}),
            'secrets': secrets + [{'name': 'DB_MIGRATE_PASSWORD', 'valueFrom': f'{ssm}/db_master_password'}],
            'logConfiguration': logs('migrate'),
        }
    ],
}
for name, data in (('app', app), ('worker', worker), ('migrate', migrate)):
    with open(os.path.join(e['TASKDEF_DIR'], f'{name}.json'), 'w') as f:
        json.dump(data, f)
PY
TASK_ARN=$(aws_q ecs register-task-definition --cli-input-json "file://${TASKDEF_DIR}/app.json" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
WORKER_ARN=$(aws_q ecs register-task-definition --cli-input-json "file://${TASKDEF_DIR}/worker.json" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
MIGRATE_ARN=$(aws_q ecs register-task-definition --cli-input-json "file://${TASKDEF_DIR}/migrate.json" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "   ${TASK_ARN##*/}"

# Tarefas nas subnets públicas com IP público: saem para a internet pelo Internet Gateway (ECR, SSM, logs,
# SMTP) sem NAT. Ninguém chega nelas de fora: o security group só aceita o ALB.
NETWORK="awsvpcConfiguration={subnets=[${SUBNET_PUB_A},${SUBNET_PUB_B}],securityGroups=[${SG_APP}],assignPublicIp=ENABLED}"

# ── 10. Migrações e serviço ───────────────────────────────────────────────────
echo "[10/10] Migrações do banco..."
# Logo depois de o ECS ser usado pela primeira vez na conta, o papel dele pode levar alguns minutos para valer
# ("Unable to assume the service linked role"): tenta de novo por até 5 minutos antes de desistir.
RUN_ARN=""
for TRY in $(seq 1 10); do
  if RUN_ARN=$(aws_q ecs run-task --cluster "${PREFIX}" --task-definition "${MIGRATE_ARN}" --launch-type FARGATE \
    --network-configuration "${NETWORK}" --query 'tasks[0].taskArn' --output text 2> "${SECRET_DIR}/run-task.err"); then
    break
  fi
  if ! grep -q "service linked role" "${SECRET_DIR}/run-task.err" || [ "${TRY}" = 10 ]; then
    cat "${SECRET_DIR}/run-task.err" >&2
    exit 1
  fi
  echo "   O papel do ECS ainda está propagando na AWS; nova tentativa em 30 s (${TRY}/10)..."
  sleep "${RETRY_SLEEP:-30}"
done
aws_q ecs wait tasks-stopped --cluster "${PREFIX}" --tasks "${RUN_ARN}"
EXIT_CODE=$(aws_q ecs describe-tasks --cluster "${PREFIX}" --tasks "${RUN_ARN}" \
  --query 'tasks[0].containers[0].exitCode' --output text)
if [ "${EXIT_CODE}" != "0" ]; then
  REASON=$(aws_q ecs describe-tasks --cluster "${PREFIX}" --tasks "${RUN_ARN}" \
    --query 'tasks[0].stoppedReason' --output text)
  echo "   As migrações falharam (código ${EXIT_CODE}: ${REASON}). O serviço não foi atualizado."
  echo "   Veja: aws logs tail ${LOG_GROUP} --log-stream-name-prefix migrate --region ${REGION}"
  exit 1
fi
echo "   Migrações e papel da aplicação em dia."

service_active() {
  aws_q ecs describe-services --cluster "${PREFIX}" --services "$1" --query 'services[0].status' --output text \
    2>/dev/null | grep -q ACTIVE
}
# Worker primeiro: na troca do formato antigo (web e worker na mesma tarefa), os dois workers convivem por
# alguns minutos sem problema (a fila e as rotinas periódicas não repetem trabalho com mais de um worker).
echo "   Serviço do worker..."
if service_active "${PREFIX}-worker"; then
  aws_q ecs update-service --cluster "${PREFIX}" --service "${PREFIX}-worker" --task-definition "${WORKER_ARN}" \
    --desired-count 1 --network-configuration "${NETWORK}" --enable-execute-command --force-new-deployment \
    > /dev/null
else
  aws_q ecs create-service --cluster "${PREFIX}" --service-name "${PREFIX}-worker" \
    --task-definition "${WORKER_ARN}" --desired-count 1 --launch-type FARGATE \
    --enable-execute-command --network-configuration "${NETWORK}" > /dev/null
fi
echo "   Serviço do site (${WEB_COUNT} tarefa(s))..."
if service_active "${PREFIX}"; then
  aws_q ecs update-service --cluster "${PREFIX}" --service "${PREFIX}" --task-definition "${TASK_ARN}" \
    --desired-count "${WEB_COUNT}" --network-configuration "${NETWORK}" --enable-execute-command \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=web,containerPort=8000" \
    --health-check-grace-period-seconds 120 --force-new-deployment > /dev/null
  echo "   Serviço atualizado."
else
  aws_q ecs create-service --cluster "${PREFIX}" --service-name "${PREFIX}" \
    --task-definition "${TASK_ARN}" --desired-count "${WEB_COUNT}" --launch-type FARGATE \
    --enable-execute-command --network-configuration "${NETWORK}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=web,containerPort=8000" \
    --health-check-grace-period-seconds 120 > /dev/null
  echo "   Serviço criado."
fi
echo "   Aguardando o serviço ficar estável..."
aws_q ecs wait services-stable --cluster "${PREFIX}" --services "${PREFIX}" "${PREFIX}-worker"
if [ "${CLOUDFRONT}" = "1" ] && { [ "${CF_CREATED}" = "1" ] || [ "${GUARD_EXISTS}" = 0 ]; }; then
  # Na criação, o endereço novo ainda não responde; na primeira vez com o cabeçalho secreto, o ALB só pode passar
  # a exigi-lo depois que o CloudFront estiver mandando. Nas demais atualizações, o site segue no ar.
  echo "   Aguardando o CloudFront terminar de publicar (5 a 15 minutos)..."
  cfq cloudfront wait distribution-deployed --id "${CF_ID}"
fi
if [ "${CLOUDFRONT}" = "1" ]; then
  # ALB: com o cabeçalho secreto, encaminha; sem ele (acesso direto ou CloudFront de outra pessoa), 403.
  GUARD_FILE="${SECRET_DIR}/guard.json"
  python3 - "${SECRET_DIR}/origin_secret" "${ORIGIN_HEADER}" "${GUARD_FILE}" <<'PY'
import json, sys

secret = open(sys.argv[1]).read().strip()
condition = {'Field': 'http-header', 'HttpHeaderConfig': {'HttpHeaderName': sys.argv[2], 'Values': [secret]}}
json.dump([condition], open(sys.argv[3], 'w'))
PY
  RULE=$(guard_rule "${GUARD_LISTENER}")
  if none "${RULE}"; then
    aws_q elbv2 create-rule --listener-arn "${GUARD_LISTENER}" --priority "${GUARD_PRIORITY}" \
      --conditions "file://${GUARD_FILE}" --actions "${FORWARD}" > /dev/null
  else
    aws_q elbv2 modify-rule --rule-arn "${RULE}" --conditions "file://${GUARD_FILE}" --actions "${FORWARD}" > /dev/null
  fi
  aws_q elbv2 modify-listener --listener-arn "${GUARD_LISTENER}" --default-actions "${DENY}" > /dev/null
  rm -f "${GUARD_FILE}"
fi

# ── Alarmes (com ALERT_EMAIL) ─────────────────────────────────────────────────
if [ -n "${ALERT_EMAIL}" ]; then
  echo "   Alarmes para ${ALERT_EMAIL}..."
  TOPIC_ARN=$(aws_q sns create-topic --name "${PREFIX}-alarmes" --query 'TopicArn' --output text)
  SUBSCRIBED=$(aws_q sns list-subscriptions-by-topic --topic-arn "${TOPIC_ARN}" \
    --query "length(Subscriptions[?Endpoint=='${ALERT_EMAIL}'])" --output text)
  if [ "${SUBSCRIBED}" = "0" ]; then
    aws_q sns subscribe --topic-arn "${TOPIC_ARN}" --protocol email --notification-endpoint "${ALERT_EMAIL}" > /dev/null
    echo "   Confirme a inscrição no e-mail que a AWS enviou para ${ALERT_EMAIL}."
  fi
  LB_DIM="${ALB_ARN#*:loadbalancer/}"
  TG_DIM="targetgroup/${TG_ARN#*:targetgroup/}"
  alarm() {  # nome, descrição, e o resto dos parâmetros do put-metric-alarm
    local NAME=$1 DESCRIPTION=$2
    shift 2
    aws_q cloudwatch put-metric-alarm --alarm-name "${PREFIX}-${NAME}" --alarm-description "${DESCRIPTION}" \
      --alarm-actions "${TOPIC_ARN}" --ok-actions "${TOPIC_ARN}" "$@"
  }
  alarm site-fora "Nenhuma tarefa saudável atrás do ALB: o site está fora do ar." \
    --namespace AWS/ApplicationELB --metric-name HealthyHostCount --statistic Minimum \
    --dimensions "Name=LoadBalancer,Value=${LB_DIM}" "Name=TargetGroup,Value=${TG_DIM}" \
    --period 60 --evaluation-periods 3 --threshold 1 --comparison-operator LessThanThreshold \
    --treat-missing-data breaching
  alarm erros-5xx "Muitas respostas de erro (5xx) da aplicação em 5 minutos." \
    --namespace AWS/ApplicationELB --metric-name HTTPCode_Target_5XX_Count --statistic Sum \
    --dimensions "Name=LoadBalancer,Value=${LB_DIM}" \
    --period 300 --evaluation-periods 1 --threshold 10 --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching
  alarm banco-espaco "O banco está com menos de 2 GB livres." \
    --namespace AWS/RDS --metric-name FreeStorageSpace --statistic Minimum \
    --dimensions "Name=DBInstanceIdentifier,Value=${DB_ID}" \
    --period 300 --evaluation-periods 1 --threshold 2147483648 --comparison-operator LessThanThreshold
  alarm banco-cpu "O banco está com a CPU acima de 80% há 15 minutos." \
    --namespace AWS/RDS --metric-name CPUUtilization --statistic Average \
    --dimensions "Name=DBInstanceIdentifier,Value=${DB_ID}" \
    --period 300 --evaluation-periods 3 --threshold 80 --comparison-operator GreaterThanThreshold
  alarm worker-parado "O worker (e-mails, lembretes e faxina) está sem tarefa rodando há 10 minutos." \
    --namespace ECS/ContainerInsights --metric-name RunningTaskCount --statistic Minimum \
    --dimensions "Name=ClusterName,Value=${PREFIX}" "Name=ServiceName,Value=${PREFIX}-worker" \
    --period 300 --evaluation-periods 2 --threshold 1 --comparison-operator LessThanThreshold \
    --treat-missing-data notBreaching  # logo depois de ligar o Container Insights ainda não há dados
fi

# ── Resultado ─────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo " Deploy concluído!"
echo " URL : ${SITE_URL}"
if [ "${CLOUDFRONT}" = "1" ] && [ "${USE_HTTPS}" = true ]; then
  echo " HTTPS pelo CloudFront até o ALB, aberto a todos. O endereço do ALB não abre mais direto."
  echo " DNS : crie um CNAME de ${DOMAIN} para ${CF_DOMAIN}"
elif [ "${CLOUDFRONT}" = "1" ]; then
  echo " HTTPS pelo CloudFront, aberto a todos. O endereço do ALB não abre mais direto."
  echo " Atenção: este endereço é provisório. Os certificados guardam o endereço no QR Code: defina um domínio"
  echo " próprio antes de emitir certificados de eventos reais."
elif [ "${USE_HTTPS}" = true ]; then
  echo " DNS : crie um CNAME de ${DOMAIN} para ${ALB_DNS}"
else
  echo " Sem HTTPS: o site só abre a partir de ${ALLOWED_CIDR}."
fi
echo " Logs: aws logs tail ${LOG_GROUP} --follow --region ${REGION}"
echo " Admin do Django: ${SITE_URL}/${ADMIN_URL}"
[ -n "${ALERT_EMAIL}" ] || echo " Sem alarmes: rode com ALERT_EMAIL=voce@exemplo.com para ser avisado se o site cair."

echo ""
echo " Criar o primeiro administrador:"
echo "   TASK=\$(aws ecs list-tasks --cluster ${PREFIX} --service-name ${PREFIX} --region ${REGION} \\"
echo "     --query 'taskArns[0]' --output text)"
echo "   aws ecs execute-command --cluster ${PREFIX} --task \$TASK --container web --interactive \\"
echo "     --command 'python manage.py createsuperuser' --region ${REGION}"
echo "   (precisa do Session Manager plugin instalado na sua máquina)"
echo ""
echo " Parar os containers (economizar): STAGE=${STAGE} ./infra/stop.sh"
echo " Destruir tudo                   : STAGE=${STAGE} ./infra/teardown.sh"
echo "=============================================="
