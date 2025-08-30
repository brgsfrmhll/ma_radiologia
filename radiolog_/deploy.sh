#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/home/ubuntu/ma_radiologia"
REPO_URL="https://github.com/brgsfrmhll/ma_radiologia.git"
VENV_PATH="/home/ubuntu/.venv"
SERVICE_RAW="portal-radiologico"   # sem .service mesmo

# ===== util =====
log(){ printf "[%(%F %T)T] %s\n" -1 "$*"; }
err(){ printf "[%(%F %T)T] [ERRO] %s\n" -1 "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || err "Comando não encontrado: $1"; }
svcname(){ [[ "$1" =~ \.service$ ]] && echo "$1" || echo "$1.service"; }
systemctl_cmd(){ [[ $EUID -ne 0 ]] && sudo systemctl "$@" || systemctl "$@"; }

# ===== checagens =====
need git
[[ -d "$(dirname "$APP_DIR")" ]] || err "Diretório pai não existe: $(dirname "$APP_DIR")"

SERVICE="$(svcname "$SERVICE_RAW")"
log "Serviço alvo: $SERVICE"

# ===== clone se necessário =====
if [[ ! -d "$APP_DIR/.git" ]]; then
  log "Repositório não encontrado. Clonando em $APP_DIR..."
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

# ===== garantir remote e descobrir branch padrão =====
git remote set-url origin "$REPO_URL"
git fetch --prune

# Descobre o HEAD remoto (ex.: origin/MAIN ou origin/master)
REMOTE_HEAD="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD || true)"
if [[ -z "$REMOTE_HEAD" ]]; then
  # fallback
  REMOTE_HEAD="$(git remote show origin | awk '/HEAD branch/ {print "origin/"$NF}')"
fi
[[ -n "$REMOTE_HEAD" ]] || err "Não foi possível detectar a branch padrão do remoto."
DEFAULT_BRANCH="${REMOTE_HEAD#origin/}"
log "Branch padrão remota: $DEFAULT_BRANCH"

# Troca para a branch padrão
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$DEFAULT_BRANCH" ]]; then
  log "Checando branch $DEFAULT_BRANCH..."
  git checkout "$DEFAULT_BRANCH"
fi

# ===== atualizar working tree =====
log "Atualizando repositório…"
# puxa com rebase rápido; se não der FF, faz rebase normal
if ! git pull --rebase --ff-only; then
  log "Fast-forward indisponível; tentando rebase tradicional…"
  git pull --rebase
fi

# ===== preparar venv e dependências =====
if [[ -f "$VENV_PATH/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_PATH/bin/activate"
  if [[ -f requirements.txt ]]; then
    log "Instalando dependências (pip)…"
    pip install -r requirements.txt
  else
    log "requirements.txt não encontrado; pulando pip install."
  fi
else
  log "Venv não encontrado em $VENV_PATH; pulando etapa de pip."
fi

# ===== reload/restart serviço =====
log "Recarregando units do systemd…"
systemctl_cmd daemon-reload

log "Reiniciando $SERVICE…"
systemctl_cmd restart "$SERVICE"

log "Status do serviço:"
# --no-pager e -l para log completo na mesma tela
systemctl_cmd status "$SERVICE" --no-pager -l || {
  err "O serviço $SERVICE não está ativo após o restart."
}

log "Deploy OK."
