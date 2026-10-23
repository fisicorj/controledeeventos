#!/usr/bin/env bash
# Para os containers ECS (zera o número de tarefas) sem destruir a infraestrutura.
# Continuam cobrando: o ALB (~US$ 16 a 22/mês), o EFS (pelo espaço usado) e o RDS (~US$ 15/mês).
# STOP_DB=1 ./infra/stop.sh também para o RDS (a AWS liga de novo sozinha depois de 7 dias).
# Outro ambiente: STAGE=prod ./infra/stop.sh

set -euo pipefail
REGION="${REGION:-us-east-1}"
STAGE="${STAGE:-test}"
CLUSTER="arena-events-${STAGE}"
SERVICE="${CLUSTER}"

for NAME in "${SERVICE}" "${SERVICE}-worker"; do
  # O worker separado só existe depois do setup.sh desta versão; antes, ele rodava junto com o web.
  aws ecs update-service --cluster "${CLUSTER}" --service "${NAME}" --desired-count 0 \
    --region "${REGION}" > /dev/null 2>&1 || [ "${NAME}" != "${SERVICE}" ] || exit 1
done
echo "Containers parados (site e worker)."
# Com os containers parados, o alarme de "site fora do ar" dispararia: as ações ficam desligadas até religar.
ALARMS=$(aws cloudwatch describe-alarms --alarm-name-prefix "${CLUSTER}-" --query 'MetricAlarms[].AlarmName' \
  --output text --region "${REGION}" 2>/dev/null || true)
if [ -n "${ALARMS}" ] && [ "${ALARMS}" != "None" ]; then
  # shellcheck disable=SC2086
  aws cloudwatch disable-alarm-actions --alarm-names ${ALARMS} --region "${REGION}"
  echo "Alarmes pausados (voltam ao rodar o setup.sh de novo)."
fi
if [ "${STOP_DB:-}" = "1" ]; then
  aws rds stop-db-instance --db-instance-identifier "${CLUSTER}-db" --region "${REGION}" > /dev/null
  echo "RDS parando (religa sozinho em 7 dias). Para ligar antes:"
  echo "  aws rds start-db-instance --db-instance-identifier ${CLUSTER}-db --region ${REGION}"
fi
echo "Para retomar: STAGE=${STAGE} ./infra/setup.sh (ou só os containers:"
echo "  aws ecs update-service --cluster ${CLUSTER} --service ${SERVICE} --desired-count 1 --region ${REGION}"
echo "  aws ecs update-service --cluster ${CLUSTER} --service ${SERVICE}-worker --desired-count 1 --region ${REGION})"
