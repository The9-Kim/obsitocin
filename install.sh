#!/bin/bash
set -e

# ── obsitocin installer (macOS) ──
# curl -fsSL https://raw.githubusercontent.com/The9-Kim/obsitocin/main/install.sh | bash

REPO_URL="https://github.com/The9-Kim/obsitocin.git"
INSTALL_DIR="$HOME/obsitocin"
MODELS_DIR="$HOME/.local/share/obsitocin/models"
QWEN_REPO="unsloth/Qwen3.5-4B-GGUF"
EMBED_REPO="Qwen/Qwen3-Embedding-0.6B-GGUF"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; exit 1; }

# ── 1. macOS 확인 ──
info "macOS 확인"
[ "$(uname)" = "Darwin" ] || fail "이 스크립트는 macOS 전용입니다."
ok "macOS $(sw_vers -productVersion)"

# ── 2. Xcode CLI tools ──
info "Xcode CLI tools 확인"
if ! xcode-select -p &>/dev/null; then
    warn "Xcode CLI tools 설치 중 (팝업이 뜨면 설치를 눌러주세요)..."
    xcode-select --install
    echo "설치 완료 후 이 스크립트를 다시 실행해주세요."
    exit 0
fi
ok "Xcode CLI tools"

# ── 3. Python 3.10+ 확인 ──
info "Python 확인"
if ! command -v python3 &>/dev/null; then
    fail "python3을 찾을 수 없습니다. brew install python 으로 설치해주세요."
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python 3.10 이상이 필요합니다. 현재: $PY_VER"
fi
ok "Python $PY_VER"

# ── 4. Homebrew ──
info "Homebrew 확인"
if ! command -v brew &>/dev/null; then
    warn "Homebrew 설치 중..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Apple Silicon: /opt/homebrew, Intel: /usr/local
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi
ok "Homebrew ($(brew --prefix))"

# ── 5. llama.cpp (llama-server) ──
info "llama-server 확인"
if ! command -v llama-server &>/dev/null; then
    warn "llama.cpp 설치 중..."
    brew install llama.cpp
fi
ok "llama-server"

# ── 6. Clone / Pull ──
info "obsitocin 소스 코드"
if [ -d "$INSTALL_DIR/.git" ]; then
    warn "기존 설치 발견 — 업데이트 중..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
ok "$INSTALL_DIR"

# ── 7. venv + pip install ──
info "Python 가상환경 설정"
cd "$INSTALL_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e ".[mcp,download]" -q
ok "pip install 완료"

# ── 8. Qwen 모델 다운로드 ──
info "Qwen 태깅 모델 다운로드 ($QWEN_REPO)"
QWEN_DIR="$MODELS_DIR/Qwen3.5-4B-GGUF"
if ls "$QWEN_DIR"/*Q4_K_M*.gguf &>/dev/null 2>&1; then
    ok "이미 다운로드됨 — 스킵"
else
    mkdir -p "$MODELS_DIR"
    huggingface-cli download "$QWEN_REPO" \
        --include '*Q4_K_M*' \
        --local-dir "$QWEN_DIR"
    ok "Qwen 모델 다운로드 완료"
fi

# ── 9. 임베딩 모델 다운로드 ──
info "임베딩 모델 다운로드 ($EMBED_REPO)"
EMBED_DIR="$MODELS_DIR/Qwen3-Embedding-0.6B-GGUF"
if ls "$EMBED_DIR"/*Q8_0*.gguf &>/dev/null 2>&1; then
    ok "이미 다운로드됨 — 스킵"
else
    huggingface-cli download "$EMBED_REPO" \
        --include '*Q8_0*' \
        --local-dir "$EMBED_DIR"
    ok "임베딩 모델 다운로드 완료"
fi

# ── 10. vault 경로 입력 ──
info "Obsidian vault 경로 설정"
DEFAULT_VAULT="$HOME/Documents/Obsitocin"
if [ -t 0 ]; then
    printf "  vault 경로 [%s]: " "$DEFAULT_VAULT"
    read -r VAULT_DIR
    VAULT_DIR="${VAULT_DIR:-$DEFAULT_VAULT}"
else
    VAULT_DIR="$DEFAULT_VAULT"
    warn "파이프 실행 감지 — 기본 경로 사용: $VAULT_DIR"
fi
ok "$VAULT_DIR"

# ── 11. obsitocin init ──
info "obsitocin 초기화"
obsitocin init --vault-dir "$VAULT_DIR" --llm-provider qwen
ok "초기화 완료"

# ── 12. shell alias ──
info "shell alias 등록"
ALIAS_LINE="alias obsitocin='$INSTALL_DIR/.venv/bin/obsitocin'"
ZSHRC="$HOME/.zshrc"
if [ -f "$ZSHRC" ] && grep -qF "alias obsitocin=" "$ZSHRC"; then
    ok "이미 등록됨 — 스킵"
else
    echo "" >> "$ZSHRC"
    echo "# obsitocin" >> "$ZSHRC"
    echo "$ALIAS_LINE" >> "$ZSHRC"
    ok "~/.zshrc에 alias 추가됨"
fi

echo ""
info "설치 완료!"
echo ""
echo "  새 터미널을 열거나 다음을 실행하세요:"
echo "    source ~/.zshrc"
echo ""
echo "  사용법:"
echo "    obsitocin status    # 상태 확인"
echo "    obsitocin run       # 대기열 처리"
echo ""
