#!/usr/bin/env bash
# CUIDADO: apaga TODA a infraestrutura de um ambiente criado pelo infra/setup.sh (padrão STAGE=test):
# ECS, ALB, RDS (com os dados), EFS (com os arquivos enviados), ECR, segredos do SSM, papéis IAM, logs e a VPC.
# Não tem volta. Para guardar uma cópia do banco antes: FINAL_SNAPSHOT=1 ./infra/teardown.sh
# Os registros de acesso (bucket S3 do ambiente) também são apagados; KEEP_LOGS=1 guarda o bucket.
# Outro ambiente: STAGE=prod ./infra/teardown.sh (a proteção contra exclusão do banco é desligada aqui).

set -euo pipefail
REGION="${REGION:-us-east-1}"
STAGE="${STAGE:-test}"
APP="arena-events-${STAGE}"
SSM_PATH="/arena-events/${STAGE}"

aws_q() { aws "$@" --region "${REGION}"; }
none() { [ -z "$1" ] || [ "$1" = "None" ]; }

echo "Destruindo o ambiente ${STAGE} do arena-events (${REGION})..."
if [ "${STAGE}" = "test" ]; then
  read -r -p "Confirmar? Digite yes: " CONFIRM
  [ "${CONFIRM}" = "yes" ] || { echo "Cancelado."; exit 0; }
else
  read -r -p "Isto apaga o banco e os arquivos do ambiente ${STAGE}. Digite o nome do ambiente para confirmar: " CONFIRM
  [ "${CONFIRM}" = "${STAGE}" ] || { echo "Cancelado."; exit 0; }
fi

# Alarmes
echo "Removendo os alarmes..."
ALARMS=$(aws_q cloudwatch describe-alarms --alarm-name-prefix "${APP}-" --query 'MetricAlarms[].AlarmName' \
  --output text 2>/dev/null || true)
if [ -n "${ALARMS}" ] && [ "${ALARMS}" != "None" ]; then
  # shellcheck disable=SC2086
  aws_q cloudwatch delete-alarms --alarm-names ${ALARMS}
fi
TOPIC_ARN=$(aws_q sns list-topics --query "Topics[?ends_with(TopicArn, ':${APP}-alarmes')].TopicArn | [0]" \
  --output text 2>/dev/null || true)
if ! none "${TOPIC_ARN}"; then aws_q sns delete-topic --topic-arn "${TOPIC_ARN}"; fi

# ECS
echo "Removendo ECS..."
for SERVICE in "${APP}" "${APP}-worker"; do
  if aws_q ecs describe-services --cluster "${APP}" --services "${SERVICE}" \
    --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
    aws_q ecs update-service --cluster "${APP}" --service "${SERVICE}" --desired-count 0 > /dev/null
    aws_q ecs delete-service --cluster "${APP}" --service "${SERVICE}" --force > /dev/null
    aws_q ecs wait services-inactive --cluster "${APP}" --services "${SERVICE}"
  fi
done
for TASK in $(aws_q ecs list-tasks --cluster "${APP}" --query 'taskArns[]' --output text 2>/dev/null || true); do
  aws_q ecs stop-task --cluster "${APP}" --task "${TASK}" > /dev/null
done
aws_q ecs delete-cluster --cluster "${APP}" > /dev/null 2>&1 || true
for FAMILY in "${APP}" "${APP}-worker" "${APP}-migrate"; do
  for TD in $(aws_q ecs list-task-definitions --family-prefix "${FAMILY}" --query 'taskDefinitionArns[]' \
    --output text); do
    aws_q ecs deregister-task-definition --task-definition "${TD}" > /dev/null
  done
done

# CloudFront (CLOUDFRONT=1) e o WAF dele. Para apagar, a AWS exige desativar e esperar a publicação (5 a 15 min).
cfq() { aws "$@" --region us-east-1; }
CF_ID=$(cfq cloudfront list-distributions --query "DistributionList.Items[?Comment=='${APP}'].Id | [0]" \
  --output text 2>/dev/null || true)
if ! none "${CF_ID}"; then
  echo "Desativando o CloudFront (5 a 15 minutos)..."
  CF_FILE=$(mktemp)
  ETAG=$(cfq cloudfront get-distribution-config --id "${CF_ID}" --query 'ETag' --output text)
  cfq cloudfront get-distribution-config --id "${CF_ID}" --query 'DistributionConfig' --output json \
    | python3 -c 'import json, sys; c = json.load(sys.stdin); c["Enabled"] = False; c["WebACLId"] = ""; print(json.dumps(c))' \
    > "${CF_FILE}"
  cfq cloudfront update-distribution --id "${CF_ID}" --if-match "${ETAG}" --distribution-config "file://${CF_FILE}" \
    > /dev/null
  rm -f "${CF_FILE}"
  cfq cloudfront wait distribution-deployed --id "${CF_ID}"
  ETAG=$(cfq cloudfront get-distribution-config --id "${CF_ID}" --query 'ETag' --output text)
  cfq cloudfront delete-distribution --id "${CF_ID}" --if-match "${ETAG}"
fi
CF_WAF_ID=$(cfq wafv2 list-web-acls --scope CLOUDFRONT --query "WebACLs[?Name=='${APP}-waf-cf'].Id | [0]" \
  --output text 2>/dev/null || true)
if ! none "${CF_WAF_ID}"; then
  LOCK=$(cfq wafv2 get-web-acl --scope CLOUDFRONT --name "${APP}-waf-cf" --id "${CF_WAF_ID}" --query 'LockToken' \
    --output text)
  cfq wafv2 delete-web-acl --scope CLOUDFRONT --name "${APP}-waf-cf" --id "${CF_WAF_ID}" --lock-token "${LOCK}"
fi

# ALB e target group
echo "Removendo o ALB..."
ALB_ARN=$(aws_q elbv2 describe-load-balancers --names "${APP}-alb" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
WAF_ID=$(aws_q wafv2 list-web-acls --scope REGIONAL --query "WebACLs[?Name=='${APP}-waf'].Id | [0]" \
  --output text 2>/dev/null || true)
if ! none "${WAF_ID}"; then
  echo "Removendo o WAF..."
  if ! none "${ALB_ARN}"; then aws_q wafv2 disassociate-web-acl --resource-arn "${ALB_ARN}" 2> /dev/null || true; fi
  LOCK=$(aws_q wafv2 get-web-acl --scope REGIONAL --name "${APP}-waf" --id "${WAF_ID}" --query 'LockToken' \
    --output text)
  aws_q wafv2 delete-web-acl --scope REGIONAL --name "${APP}-waf" --id "${WAF_ID}" --lock-token "${LOCK}"
fi
if ! none "${ALB_ARN}"; then
  for L in $(aws_q elbv2 describe-listeners --load-balancer-arn "${ALB_ARN}" \
    --query 'Listeners[*].ListenerArn' --output text); do
    aws_q elbv2 delete-listener --listener-arn "${L}"
  done
  aws_q elbv2 delete-load-balancer --load-balancer-arn "${ALB_ARN}"
  aws_q elbv2 wait load-balancers-deleted --load-balancer-arns "${ALB_ARN}"
fi
TG_ARN=$(aws_q elbv2 describe-target-groups --names "${APP}-tg" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if ! none "${TG_ARN}"; then aws_q elbv2 delete-target-group --target-group-arn "${TG_ARN}"; fi

# RDS
echo "Removendo o RDS (alguns minutos)..."
if aws_q rds describe-db-instances --db-instance-identifier "${APP}-db" > /dev/null 2>&1; then
  PROTECTED=$(aws_q rds describe-db-instances --db-instance-identifier "${APP}-db" \
    --query 'DBInstances[0].DeletionProtection' --output text)
  if [ "${PROTECTED}" = "True" ]; then
    aws_q rds modify-db-instance --db-instance-identifier "${APP}-db" --no-deletion-protection \
      --apply-immediately > /dev/null
    aws_q rds wait db-instance-available --db-instance-identifier "${APP}-db"
  fi
  if [ "${FINAL_SNAPSHOT:-}" = "1" ]; then
    SNAP="${APP}-final-$(date +%Y%m%d%H%M%S)"
    aws_q rds delete-db-instance --db-instance-identifier "${APP}-db" \
      --final-db-snapshot-identifier "${SNAP}" > /dev/null
    echo "   Cópia final do banco: snapshot ${SNAP} (continua cobrando o armazenamento até ser apagado)."
  else
    aws_q rds delete-db-instance --db-instance-identifier "${APP}-db" --skip-final-snapshot > /dev/null
  fi
  aws_q rds wait db-instance-deleted --db-instance-identifier "${APP}-db"
fi
aws_q rds delete-db-subnet-group --db-subnet-group-name "${APP}-db-sng" 2>/dev/null || true

# EFS
echo "Removendo o EFS (arquivos enviados)..."
EFS_ID=$(aws_q efs describe-file-systems --creation-token "${APP}-files" \
  --query 'FileSystems[0].FileSystemId' --output text 2>/dev/null || true)
if ! none "${EFS_ID}"; then
  for AP in $(aws_q efs describe-access-points --file-system-id "${EFS_ID}" \
    --query 'AccessPoints[].AccessPointId' --output text); do
    aws_q efs delete-access-point --access-point-id "${AP}"
  done
  for MT in $(aws_q efs describe-mount-targets --file-system-id "${EFS_ID}" \
    --query 'MountTargets[].MountTargetId' --output text); do
    aws_q efs delete-mount-target --mount-target-id "${MT}"
  done
  until [ "$(aws_q efs describe-mount-targets --file-system-id "${EFS_ID}" \
    --query 'length(MountTargets)' --output text)" = "0" ]; do sleep 5; done
  aws_q efs delete-file-system --file-system-id "${EFS_ID}"
fi

# ECR
echo "Removendo o ECR..."
aws_q ecr delete-repository --repository-name "${APP}" --force > /dev/null 2>&1 || true

# SSM
echo "Removendo os segredos do SSM..."
for P in secret_key field_encryption_key db_password db_master_password db_host allowed_hosts admin_url origin_secret; do
  aws_q ssm delete-parameter --name "${SSM_PATH}/${P}" 2>/dev/null || true
done

# IAM
echo "Removendo os papéis IAM..."
EXEC_ROLE="${APP}-ecs-exec-role"
aws iam detach-role-policy --role-name "${EXEC_ROLE}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true
aws iam delete-role-policy --role-name "${EXEC_ROLE}" --policy-name ssm-read 2>/dev/null || true
aws iam delete-role --role-name "${EXEC_ROLE}" 2>/dev/null || true
TASK_ROLE="${APP}-ecs-task-role"
aws iam delete-role-policy --role-name "${TASK_ROLE}" --policy-name exec-and-efs 2>/dev/null || true
aws iam delete-role --role-name "${TASK_ROLE}" 2>/dev/null || true

# Logs
aws_q logs delete-log-group --log-group-name "/ecs/${APP}" 2>/dev/null || true

# Registros de acesso do ALB e do CloudFront (depois do ALB, que grava os últimos ao ser apagado)
LOG_BUCKET="${APP}-logs-$(aws sts get-caller-identity --query Account --output text)"
if [ "${KEEP_LOGS:-0}" != "1" ] && aws_q s3api head-bucket --bucket "${LOG_BUCKET}" > /dev/null 2>&1; then
  echo "Removendo os registros de acesso (s3://${LOG_BUCKET})..."
  aws_q s3 rm "s3://${LOG_BUCKET}" --recursive --quiet
  aws_q s3api delete-bucket --bucket "${LOG_BUCKET}"
fi

# VPC
echo "Removendo a VPC..."
VPC_ID=$(aws_q ec2 describe-vpcs --filters "Name=tag:Name,Values=${APP}-vpc" \
  --query 'Vpcs[0].VpcId' --output text)
if ! none "${VPC_ID}"; then
  # As interfaces de rede do ECS, do RDS e do EFS somem alguns minutos depois dos recursos.
  for _ in $(seq 1 60); do
    LEFT=$(aws_q ec2 describe-network-interfaces --filters "Name=vpc-id,Values=${VPC_ID}" \
      --query 'length(NetworkInterfaces)' --output text)
    [ "${LEFT}" = "0" ] && break
    sleep 10
  done
  SGS=$(aws_q ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" \
    --query "SecurityGroups[?GroupName!='default'].GroupId" --output text)
  # Primeiro as regras que apontam de um grupo para outro; depois os grupos.
  for SG in ${SGS}; do
    RULES=$(aws_q ec2 describe-security-group-rules --filters "Name=group-id,Values=${SG}" \
      --query 'SecurityGroupRules[?!IsEgress].SecurityGroupRuleId' --output text)
    if [ -n "${RULES}" ]; then
      # shellcheck disable=SC2086
      aws_q ec2 revoke-security-group-ingress --group-id "${SG}" --security-group-rule-ids ${RULES} > /dev/null
    fi
  done
  for SG in ${SGS}; do aws_q ec2 delete-security-group --group-id "${SG}"; done
  for SUBNET in $(aws_q ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" \
    --query 'Subnets[].SubnetId' --output text); do
    aws_q ec2 delete-subnet --subnet-id "${SUBNET}"
  done
  for RT in $(aws_q ec2 describe-route-tables --filters "Name=vpc-id,Values=${VPC_ID}" \
    --query 'RouteTables[?!(Associations[?Main])].RouteTableId' --output text); do
    aws_q ec2 delete-route-table --route-table-id "${RT}"
  done
  for IGW in $(aws_q ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=${VPC_ID}" \
    --query 'InternetGateways[].InternetGatewayId' --output text); do
    aws_q ec2 detach-internet-gateway --internet-gateway-id "${IGW}" --vpc-id "${VPC_ID}"
    aws_q ec2 delete-internet-gateway --internet-gateway-id "${IGW}"
  done
  aws_q ec2 delete-vpc --vpc-id "${VPC_ID}"
fi

echo ""
echo "Ambiente ${STAGE} removido."
