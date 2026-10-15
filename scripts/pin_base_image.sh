#!/usr/bin/env bash
# Fixa a imagem base do Dockerfile pelo digest (SEC-20): o build passa a usar sempre a mesma imagem, mesmo que
# a etiqueta python:3.12-slim seja movida. Rode de novo para atualizar (o Dependabot avisa quando sair versão nova).
#
#   bash scripts/pin_base_image.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=python:3.12-slim
docker pull --quiet "$IMAGE" > /dev/null
DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE")  # python@sha256:...
PINNED="$IMAGE@${DIGEST#*@}"
sed -i.bak -E "s|^FROM python:3\.12-slim(@sha256:[0-9a-f]+)?\$|FROM $PINNED|" Dockerfile
rm -f Dockerfile.bak
grep '^FROM' Dockerfile
