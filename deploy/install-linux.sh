#!/usr/bin/env bash
#
# Install SoftKVM as a systemd service on Linux.
#
#   ./deploy/install-linux.sh              # user service (RECOMMENDED - full KVM)
#   ./deploy/install-linux.sh --system     # system service (headless, no screen control)
#   ./deploy/install-linux.sh --check      # diagnose an existing install
#
# The user service is the right choice for a KVM: a system service runs in
# session 0 with no access to the graphical session, so screen capture and
# mouse/keyboard injection are unavailable there.

set -uo pipefail

MODE="user"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="softkvm"
PORT="${SOFTKVM_PORT:-3699}"

for arg in "$@"; do
  case "$arg" in
    --system) MODE="system" ;;
    --user)   MODE="user" ;;
    --check)  MODE="check" ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

say()  { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }
die()  { printf '\n!! %s\n' "$1" >&2; exit 1; }

if [ "${MODE}" = "check" ]; then
  exec bash "${PROJECT_DIR}/deploy/check.sh"
fi

# --------------------------------------------------------------- preflight
say "Checking prerequisites"

if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is not installed.  Fix with:  sudo apt-get install -y python3 python3-venv python3-pip"
fi
ok "python3 $(python3 --version 2>&1 | awk '{print $2}')"

# python3-venv is NOT part of a default Ubuntu install - without it the
# `python3 -m venv` call below fails and the script exits.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "    the 'venv' module is missing - this is required"
  if command -v apt-get >/dev/null 2>&1; then
    echo "    installing: sudo apt-get install -y python3-venv python3-pip"
    if command -v sudo >/dev/null 2>&1; then
      sudo apt-get update -qq || true
      sudo apt-get install -y python3-venv python3-pip || die "could not install python3-venv"
    else
      die "run: sudo apt-get install -y python3-venv python3-pip"
    fi
  else
    die "install the venv module for your distribution (python3-venv / python3-devel)"
  fi
fi
ok "venv module available"

# -------------------------------------------------------------- venv + deps
say "Installing dependencies"
VENV="${PROJECT_DIR}/.venv"
if [ ! -x "${VENV}/bin/python" ]; then
  python3 -m venv "${VENV}" || die "could not create the virtualenv at ${VENV}"
fi
ok "virtualenv at ${VENV}"

"${VENV}/bin/pip" install --upgrade pip -q || true
"${VENV}/bin/pip" install -q -r "${PROJECT_DIR}/requirements.txt" \
  || die "pip install failed - scroll up for the error"
ok "requirements installed"

"${VENV}/bin/python" -c "import flask, mss, psutil, PIL, pynput, waitress, qrcode" \
  || die "a dependency is still missing - run the pip install above manually"
ok "all imports verified"

# --------------------------------------------------------------- user mode
if [ "${MODE}" = "user" ]; then
  DATA_DIR="${HOME}/.local/share/softkvm"
  UNIT_SRC="${PROJECT_DIR}/deploy/softkvm-user.service"
  UNIT_DIR="${HOME}/.config/systemd/user"

  say "Installing the user service"
  mkdir -p "${UNIT_DIR}" "${DATA_DIR}" || die "could not create ${UNIT_DIR}"

  sed -e "s|%h/softkvm|${PROJECT_DIR}|g" \
      -e "s|%h/.local/share/softkvm|${DATA_DIR}|g" \
      -e "s|SOFTKVM_PORT=\"[0-9]*\"|SOFTKVM_PORT=\"${PORT}\"|g" \
      "${UNIT_SRC}" > "${UNIT_DIR}/${SERVICE_NAME}.service" \
    || die "could not write the unit file"

  if grep -q "%h/softkvm" "${UNIT_DIR}/${SERVICE_NAME}.service"; then
    die "the unit file still has an unsubstituted placeholder"
  fi
  ok "unit written to ${UNIT_DIR}/${SERVICE_NAME}.service"

  systemctl --user daemon-reload || die "cannot reach the user systemd instance"
  systemctl --user enable "${SERVICE_NAME}.service" || die "could not enable the service"
  systemctl --user restart "${SERVICE_NAME}.service" || die "could not start the service"

  sleep 3
  ACTIVE=$(systemctl --user is-active "${SERVICE_NAME}" 2>/dev/null)
  if [ "${ACTIVE}" = "active" ]; then
    ok "service is active"
  else
    echo
    echo "!! The service did not start (state: ${ACTIVE})."
    echo "   The reason is below - fix it and run:"
    echo "     systemctl --user restart ${SERVICE_NAME}"
    echo
    journalctl --user -u "${SERVICE_NAME}" -n 40 --no-pager
    echo
    echo "   For a full diagnosis run:  ./deploy/check.sh"
    exit 1
  fi

  # Linger keeps the service alive after logout.
  if command -v loginctl >/dev/null 2>&1; then
    if loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
      ok "linger is enabled"
    else
      echo "    enable lingering so the service survives logout:"
      echo "      sudo loginctl enable-linger $USER"
    fi
  fi

  say "Done"
  echo "    status  : systemctl --user status ${SERVICE_NAME}"
  echo "    logs    : journalctl --user -u ${SERVICE_NAME} -f"
  echo "    reset pw: cd ${PROJECT_DIR} && .venv/bin/python server.py --reset-password"
  echo

  _print_urls
  _headless_note
  exit 0
fi

# ------------------------------------------------------------- system mode
DATA_DIR="/var/lib/softkvm"
UNIT_SRC="${PROJECT_DIR}/deploy/softkvm.service"
UNIT_DIR="/etc/systemd/system"
INSTALL_DIR="/opt/softkvm"

say "Installing the system service (telemetry only - no screen control)"
command -v sudo >/dev/null 2>&1 || die "sudo is required for --system"

sudo useradd --system --home "${INSTALL_DIR}" --shell /usr/sbin/nologin softkvm 2>/dev/null || true
sudo mkdir -p "${INSTALL_DIR}" "${DATA_DIR}" || die "could not create the install directories"

# rsync is not installed on minimal images - plain cp is enough.
sudo cp -a "${PROJECT_DIR}/." "${INSTALL_DIR}/" || die "could not copy the project"
sudo rm -rf "${INSTALL_DIR}/.venv" "${INSTALL_DIR}/data" "${INSTALL_DIR}/.git" "${INSTALL_DIR}/__pycache__"
sudo mkdir -p "${DATA_DIR}"
sudo chown -R softkvm:softkvm "${INSTALL_DIR}" "${DATA_DIR}"

sudo -u softkvm python3 -m venv "${INSTALL_DIR}/.venv" || die "could not create the virtualenv"
sudo -u softkvm "${INSTALL_DIR}/.venv/bin/pip" install -q --upgrade pip || true
sudo -u softkvm "${INSTALL_DIR}/.venv/bin/pip" install -q -r "${INSTALL_DIR}/requirements.txt" \
  || die "pip install failed"

sudo sed -e "s|/opt/softkvm|${INSTALL_DIR}|g" \
        -e "s|/var/lib/softkvm|${DATA_DIR}|g" \
        -e "s|SOFTKVM_PORT=\"[0-9]*\"|SOFTKVM_PORT=\"${PORT}\"|g" \
        "${UNIT_SRC}" > "${UNIT_DIR}/${SERVICE_NAME}.service"

sudo systemctl daemon-reload || die "systemctl daemon-reload failed"
sudo systemctl enable --now "${SERVICE_NAME}.service" || die "could not start the service"

sleep 3
ACTIVE=$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null)
if [ "${ACTIVE}" != "active" ]; then
  echo
  echo "!! The service did not start (state: ${ACTIVE}). Reason:"
  echo
  sudo journalctl -u "${SERVICE_NAME}" -n 40 --no-pager
  exit 1
fi

say "Done"
echo "    status  : systemctl status ${SERVICE_NAME}"
echo "    logs    : journalctl -u ${SERVICE_NAME} -f"
echo "    reset pw: sudo -u softkvm ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/server.py --reset-password"
echo
_print_urls
_headless_note

# ------------------------------------------------------------------ helpers
_print_urls() {
  echo "    Open SoftKVM at:"
  if command -v ip >/dev/null 2>&1; then
    ip -4 -o addr show scope global 2>/dev/null |
      awk '{split($4,a,"/"); print "      http://" a[1] ":" PORT}' | sort -u
  fi
  echo "      http://127.0.0.1:${PORT}"
  echo
}

_headless_note() {
  local sessions
  sessions=$(loginctl list-sessions --no-legend 2>/dev/null | wc -l)
  if [ "${sessions:-0}" -eq 0 ]; then
    echo "    NOTE: no graphical session is logged in on this machine."
    echo "          The dashboard and telemetry work fine, but the screen"
    echo "          stream and mouse/keyboard control need a real desktop"
    echo "          session. On a headless box you can still use Xvfb:"
    echo "            sudo apt-get install -y xvfb"
    echo "            Xvfb :99 -screen 0 1920x1080x24 &"
    echo "            export DISPLAY=:99"
    echo
  fi
}
