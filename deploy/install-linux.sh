#!/usr/bin/env bash
#
# Install SoftKVM as a systemd service on Linux.
#
#   ./deploy/install-linux.sh              # user service (RECOMMENDED - full KVM)
#   ./deploy/install-linux.sh --system     # system service (headless, no screen control)
#
# The user service is the right choice for a KVM: a system service runs in
# session 0 with no access to the graphical session, so screen capture and
# mouse/keyboard injection are unavailable there.

set -euo pipefail

MODE="user"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="softkvm"
PORT="${SOFTKVM_PORT:-3699}"

for arg in "$@"; do
  case "$arg" in
    --system) MODE="system" ;;
    --user)   MODE="user" ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

echo "==> Installing dependencies"
python3 -m venv "${PROJECT_DIR}/.venv"
"${PROJECT_DIR}/.venv/bin/pip" install --upgrade pip
"${PROJECT_DIR}/.venv/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

if [ "${MODE}" = "user" ]; then
  DATA_DIR="${HOME}/.local/share/softkvm"
  UNIT_SRC="${PROJECT_DIR}/deploy/softkvm-user.service"
  UNIT_DIR="${HOME}/.config/systemd/user"

  echo "==> Installing the user service (screen control works here)"
  mkdir -p "${UNIT_DIR}" "${DATA_DIR}"
  sed -e "s|%h/softkvm|${PROJECT_DIR}|g" \
      -e "s|%h/.local/share/softkvm|${DATA_DIR}|g" \
      "${UNIT_SRC}" > "${UNIT_DIR}/${SERVICE_NAME}.service"

  systemctl --user daemon-reload
  systemctl --user enable "${SERVICE_NAME}.service"
  systemctl --user restart "${SERVICE_NAME}.service"

  echo
  echo "Installed. Check it with:"
  echo "  systemctl --user status ${SERVICE_NAME}"
  echo "  journalctl --user -u ${SERVICE_NAME} -f"
  echo
  echo "NOTE: enable lingering so the service survives logout:"
  echo "  sudo loginctl enable-linger \$USER"
else
  DATA_DIR="/var/lib/softkvm"
  UNIT_SRC="${PROJECT_DIR}/deploy/softkvm.service"
  UNIT_DIR="/etc/systemd/system"
  INSTALL_DIR="/opt/softkvm"

  echo "==> Installing the system service (telemetry only - no screen control)"
  sudo useradd --system --home "${INSTALL_DIR}" --shell /usr/sbin/nologin softkvm 2>/dev/null || true
  sudo mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
  sudo rsync -a --exclude '.venv' --exclude 'data' --exclude '.git' \
      "${PROJECT_DIR}/" "${INSTALL_DIR}/"

  sudo mkdir -p "${DATA_DIR}"
  sudo chown -R softkvm:softkvm "${INSTALL_DIR}" "${DATA_DIR}"
  sudo -u softkvm python3 -m venv "${INSTALL_DIR}/.venv"
  sudo -u softkvm "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

  sudo sed -e "s|/opt/softkvm|${INSTALL_DIR}|g" \
      -e "s|/var/lib/softkvm|${DATA_DIR}|g" \
      "${UNIT_SRC}" > "${UNIT_DIR}/${SERVICE_NAME}.service"

  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE_NAME}.service"

  echo
  echo "Installed. Check it with:"
  echo "  systemctl status ${SERVICE_NAME}"
  echo "  journalctl -u ${SERVICE_NAME} -f"
fi

echo
echo "SoftKVM listens on port ${PORT}."
echo "The first-run password is printed in the service log:"
echo "  journalctl ${SERVICE_NAME} | grep -i password    # system service"
echo "  journalctl --user -u ${SERVICE_NAME} | grep -i password   # user service"
