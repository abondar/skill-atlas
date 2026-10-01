#!/usr/bin/env bash
# Air cloud environment startup for skill-atlas.
#
# Installs (userspace, no sudo) what the image lacks to match CI:
#   - git >= 2.45 from the git-core PPA (tests expect `%cI` to print UTC as `Z`;
#     the image ships git 2.43, which prints `+00:00`);
#   - the shared libraries and fonts Playwright's Chromium needs (`playwright install --with-deps`
#     needs root, so the .debs are unpacked into ~/.local instead).
# Then syncs the Python env (uv) and the e2e toolchain (npm + Playwright Chromium).
# In the warmup run, `healthcheck` runs the full check suite from AGENTS.md.
set -euo pipefail

if [ "${AIR_STARTUP_MODE:-}" = warmup ]; then WARMUP=1; else WARMUP=; fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AIR_HOME="$HOME/.local/air"
APT_DIR="$AIR_HOME/apt"
GIT_ROOT="$AIR_HOME/git-root"
LIB_ROOT="$AIR_HOME/chromium-libs"
ENV_FILE="$HOME/.air-skill-atlas-env.sh"

log() { echo "[startup $(date +%H:%M:%S)] $*"; }

GIT_PKGS="git"
CHROMIUM_PKGS="libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64
  libcups2t64 libdbus-1-3 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 libxdamage1
  libxfixes3 libxrandr2 libasound2t64 libpango-1.0-0 libcairo2 libx11-6 libxcb1 libxext6
  fontconfig-config fonts-liberation fonts-freefont-ttf fonts-noto-color-emoji fonts-dejavu-core"

apt_user() {
  apt-get \
    -o Dir::Etc::SourceList=/dev/null \
    -o Dir::Etc::SourceParts="$APT_DIR/etc" \
    -o Dir::State::Lists="$APT_DIR/lists" \
    -o Dir::Cache="$APT_DIR/cache" \
    -o Debug::NoLocking=1 "$@"
}

install_user_packages() {
  local stamp="$AIR_HOME/.packages-installed"
  local want="$GIT_PKGS $CHROMIUM_PKGS"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ]; then
    log "userspace packages already installed"
    return
  fi
  log "installing userspace packages (git from git-core PPA, Chromium libraries)"
  mkdir -p "$APT_DIR/etc" "$APT_DIR/lists/partial" "$APT_DIR/cache/archives/partial"
  cp /etc/apt/sources.list.d/ubuntu.sources "$APT_DIR/etc/"
  curl -fsSL -o "$APT_DIR/git-core.asc" \
    "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xE1DD270288B4E6030699E45FA1715D88E1DF1F24"
  cat > "$APT_DIR/etc/git-core.sources" <<EOF
Types: deb
URIs: https://ppa.launchpadcontent.net/git-core/ppa/ubuntu/
Suites: noble
Components: main
Signed-By: $APT_DIR/git-core.asc
EOF
  apt_user update -q
  rm -f "$APT_DIR"/cache/archives/*.deb
  # shellcheck disable=SC2086
  apt_user install -y -q --download-only $want
  rm -rf "$GIT_ROOT" "$LIB_ROOT"
  mkdir -p "$GIT_ROOT" "$LIB_ROOT"
  local deb
  for deb in "$APT_DIR"/cache/archives/*.deb; do
    case "$(basename "$deb")" in
      git_*) dpkg-deb -x "$deb" "$GIT_ROOT" ;;
      git-man_*) ;;
      *) dpkg-deb -x "$deb" "$LIB_ROOT" ;;
    esac
  done
  mkdir -p "$AIR_HOME/bin"
  cat > "$AIR_HOME/bin/git" <<EOF
#!/bin/sh
GIT_EXEC_PATH="$GIT_ROOT/usr/lib/git-core" exec "$GIT_ROOT/usr/bin/git" "\$@"
EOF
  chmod +x "$AIR_HOME/bin/git"
  # The image has no fonts and no fontconfig config; without them Chromium's renderer crashes.
  cat > "$AIR_HOME/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>$LIB_ROOT/usr/share/fonts</dir>
  <cachedir>$HOME/.cache/fontconfig</cachedir>
  <include ignore_missing="yes">$LIB_ROOT/etc/fonts/conf.d</include>
</fontconfig>
EOF
  echo "$want" > "$stamp"
  log "installed $("$AIR_HOME/bin/git" --version)"
}

write_env_file() {
  cat > "$ENV_FILE" <<EOF
# skill-atlas Air environment (written by .air/cloud/startup.sh)
case ":\$PATH:" in *":$AIR_HOME/bin:"*) ;; *) export PATH="$AIR_HOME/bin:\$PATH" ;; esac
case ":\${LD_LIBRARY_PATH:-}:" in
  *":$LIB_ROOT/usr/lib/x86_64-linux-gnu:"*) ;;
  *) export LD_LIBRARY_PATH="$LIB_ROOT/usr/lib/x86_64-linux-gnu\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" ;;
esac
export FONTCONFIG_FILE="$AIR_HOME/fonts.conf"
EOF
  local line=". \"$ENV_FILE\"  # air-skill-atlas-env"
  local profile=""
  for f in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
    if [ -f "$f" ]; then profile="$f"; break; fi
  done
  [ -n "$profile" ] || profile="$HOME/.profile"
  for f in "$profile" "$HOME/.bashrc"; do
    grep -qF "# air-skill-atlas-env" "$f" 2>/dev/null || echo "$line" >> "$f"
  done
  # shellcheck disable=SC1090
  . "$ENV_FILE"
}

sync_python() {
  log "uv sync --locked"
  (cd "$REPO_DIR" && uv sync --locked)
}

sync_e2e() {
  local stamp="$REPO_DIR/e2e/node_modules/.air-lock-hash"
  local hash
  hash="$(sha256sum "$REPO_DIR/e2e/package-lock.json" | cut -d' ' -f1)"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$hash" ]; then
    log "e2e node_modules up to date"
  else
    log "npm ci (e2e)"
    (cd "$REPO_DIR/e2e" && npm ci --no-audit --no-fund)
    echo "$hash" > "$stamp"
  fi
  log "playwright install chromium"
  (cd "$REPO_DIR/e2e" && npx playwright install chromium)
}

healthcheck() {
  log "healthcheck: ruff, mypy, pytest, Playwright"
  cd "$REPO_DIR"
  git --version
  uv run skill-atlas --help > /dev/null
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy
  uv run pytest
  (cd e2e && npx playwright test --reporter=line)
  log "healthcheck passed"
}

install_user_packages
write_env_file
sync_python
sync_e2e

if [ -n "$WARMUP" ]; then healthcheck; fi
log "startup done"
