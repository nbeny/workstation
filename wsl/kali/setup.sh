#!/usr/bin/env bash
# Auto-setup idempotent pour Kali Linux (WSL).
# - Installe/verifie les paquets de base et le shell style Kali
# - Deploie les dotfiles (.zshrc / .bashrc / .profile) depuis ce depot
# Aucun secret n'est contenu ni requis par ce script. Rejouable sans risque.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTFILES_DIR="$SCRIPT_DIR/dotfiles"

say()  { printf '\n==> %s\n' "$1"; }
ok()   { printf '    [ok] %s\n' "$1"; }
skip() { printf '    [--] %s\n' "$1"; }

# ---------------------------------------------------------------------------
# Paquets apt (shell + outils de confort). Idempotent : apt n'agit que si absent.
# ---------------------------------------------------------------------------
APT_PACKAGES=(
  zsh
  zsh-syntax-highlighting
  zsh-autosuggestions
  git
  curl
  wget
  vim
  tmux
  bash-completion
)

install_apt() {
  say "Paquets apt"
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "    apt-get introuvable — distribution non Debian/Kali ?"
    return
  fi
  local missing=()
  for p in "${APT_PACKAGES[@]}"; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      skip "$p deja installe"
    else
      missing+=("$p")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "    installation : ${missing[*]}"
    sudo apt-get update -y
    sudo apt-get install -y "${missing[@]}"
    ok "paquets installes"
  fi
}

# ---------------------------------------------------------------------------
# Deploiement des dotfiles (sauvegarde l'existant avant ecrasement)
# ---------------------------------------------------------------------------
deploy_dotfile() {
  local src="$1" dst="$2"
  if [ ! -f "$src" ]; then echo "    source absente : $src"; return; fi
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    skip "a jour : $dst"
    return
  fi
  if [ -f "$dst" ]; then
    cp "$dst" "$dst.bak-$(date +%Y%m%d%H%M%S)"
  fi
  cp "$src" "$dst"
  ok "deploye : $dst"
}

deploy_dotfiles() {
  say "Dotfiles (style terminal Kali)"
  deploy_dotfile "$DOTFILES_DIR/zshrc"   "$HOME/.zshrc"
  deploy_dotfile "$DOTFILES_DIR/bashrc"  "$HOME/.bashrc"
  deploy_dotfile "$DOTFILES_DIR/profile" "$HOME/.profile"
}

# ---------------------------------------------------------------------------
# Shell par defaut -> zsh (optionnel, seulement si zsh present)
# ---------------------------------------------------------------------------
set_default_shell() {
  say "Shell par defaut"
  local zsh_path
  zsh_path="$(command -v zsh || true)"
  if [ -z "$zsh_path" ]; then skip "zsh absent, on garde le shell courant"; return; fi
  if [ "${SHELL:-}" = "$zsh_path" ]; then
    skip "zsh deja par defaut"
    return
  fi
  if chsh -s "$zsh_path" 2>/dev/null; then
    ok "shell par defaut -> zsh (effectif a la prochaine session)"
  else
    skip "chsh non disponible — definissez zsh manuellement si souhaite"
  fi
}

say "Auto-setup Kali Linux (WSL)"
install_apt
deploy_dotfiles
set_default_shell
printf '\nTermine.\n'
