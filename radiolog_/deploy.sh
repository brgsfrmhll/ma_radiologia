#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/home/ubuntu/ma_radiologia"
VENV_PATH="/home/ubuntu/.venv"
SERVICE="portal-radiologico.service"

log(){ printf "[%(%F %T)T] %s\n" -1 "$*"; }
err(){ printf "[%(%F %T)T] [ERRO] %s\n" -1 "$*" >&2; exit 1; }

# ===== Atualiza repositório =====
cd "$APP_DIR" || err "Diretório não encontrado: $APP_DIR"
log "Atualizando repositório..."
git fetch --prune
git pull --rebase || git pull

# ===== Atualiza dependências =====
if [[ -f "$VENV_PATH/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_PATH/bin/activate"
  if [[ -f requirements.txt ]]; then
    log "Instalando dependências..."
    pip install -r requirements.txt
  fi
else
  log "Venv não encontrado em $VENV_PATH, pulando pip install"
fi

# ===== Reinicia serviço =====
log "Recarregando units..."
sudo systemctl daemon-reload

log "Reiniciando $SERVICE..."
sudo systemctl restart "$SERVICE"

log "Status:"
sudo systemctl status "$SERVICE" --no-pager -l
