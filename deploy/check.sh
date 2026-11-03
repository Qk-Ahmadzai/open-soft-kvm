#!/usr/bin/env bash
#
# Diagnose a SoftKVM install on Linux.
#
#   ./deploy/check.sh
#
# Prints a PASS / FAIL line per check and ends with the exact commands to
# follow up on whatever failed.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="softkvm"
PASS=0
FAIL=0
WARN=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; WARN=$((WARN+1)); }
info() { printf '        %s\n' "$1"; }

echo
echo "SoftKVM diagnostics"
echo "==================="
echo
echo "Project directory : $PROJECT_DIR"
echo

# --- 1. python -----------------------------------------------------------
echo "1. Python"
if command -v python3 >/dev/null 2>&1; then
  ok "python3 found ($(python3 --version 2>&1))"
  if python3 -c "import ensurepip" >/dev/null 2>&1; then
    ok "venv module available"
  else
    bad "the venv module is missing (python3 -m venv will fail)"
    info "fix: sudo apt-get install -y python3-venv python3-pip"
  fi
else
  bad "python3 not found"
  info "fix: sudo apt-get install -y python3 python3-venv python3-pip"
fi
echo

# --- 2. virtualenv ------------------------------------------------------
echo "2. Virtual environment"
VENV="${PROJECT_DIR}/.venv"
if [ -x "${VENV}/bin/python" ]; then
  ok "virtualenv present at ${VENV}"
  if "${VENV}/bin/python" -c "import flask, mss, psutil, PIL, pynput" >/dev/null 2>&1; then
    ok "core dependencies import cleanly"
  else
    bad "some dependencies are missing or broken"
    info "fix: ${VENV}/bin/pip install -r ${PROJECT_DIR}/requirements.txt"
    info "missing: $(${VENV}/bin/python -c "
for m in ('flask','mss','psutil','PIL','pynput','waitress','qrcode'):
    try:
        __import__(m)
    except Exception:
        print(m, end=' ')
" 2>/dev/null)"
  fi
  if "${VENV}/bin/python" -c "import waitress" >/dev/null 2>&1; then
    ok "waitress present (production WSGI server)"
  else
    warn "waitress is not installed - the Flask dev server will be used"
    info "fix: ${VENV}/bin/pip install waitress"
  fi
else
  bad "no virtualenv at ${VENV}"
  info "fix: python3 -m venv ${VENV} && ${VENV}/bin/pip install -r ${PROJECT_DIR}/requirements.txt"
fi
echo

# --- 3. service unit -----------------------------------------------------
echo "3. Service unit"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_PATH="${UNIT_DIR}/${SERVICE_NAME}.service"
if [ -f "${UNIT_PATH}" ]; then
  ok "unit file at ${UNIT_PATH}"
  if grep -q "%h/softkvm" "${UNIT_PATH}"; then
    bad "the unit still contains the unsubstituted %h/softkvm placeholder"
    info "re-run: ./deploy/install-linux.sh"
  else
    ok "paths substituted"
  fi
  EXEC=$(grep -E '^ExecStart=' "${UNIT_PATH}" | head -1 | sed 's/^ExecStart=//')
  info "ExecStart: ${EXEC}"
  EXEC_PY=$(echo "${EXEC}" | awk '{print $1}')
  if [ -x "${EXEC_PY}" ]; then
    ok "the interpreter in ExecStart exists and is executable"
  else
    bad "ExecStart points at a missing interpreter: ${EXEC_PY}"
    info "re-run: ./deploy/install-linux.sh"
  fi
else
  bad "no unit file at ${UNIT_PATH}"
  info "fix: ./deploy/install-linux.sh"
fi
echo

# --- 4. systemd ----------------------------------------------------------
echo "4. systemd"
if systemctl --user daemon-reload >/dev/null 2>&1; then
  ok "the user systemd instance is reachable"
else
  bad "cannot talk to the user systemd instance"
  info "fix: sudo loginctl enable-linger \$USER   (then log out and back in)"
fi

if systemctl --user is-enabled "${SERVICE_NAME}" >/dev/null 2>&1; then
  ok "service is enabled at login"
else
  warn "service is not enabled - it will not start automatically"
  info "fix: systemctl --user enable ${SERVICE_NAME}"
fi

ACTIVE=$(systemctl --user is-active "${SERVICE_NAME}" 2>/dev/null)
if [ "${ACTIVE}" = "active" ]; then
  ok "service is running"
  systemctl --user status "${SERVICE_NAME}" --no-pager --lines=5 | sed 's/^/        /'
else
  bad "service is '${ACTIVE:-unknown}', not active"
  info "the reason is usually in the log - run:"
  info "  journalctl --user -u ${SERVICE_NAME} -n 40 --no-pager"
fi
echo

# --- 5. port -------------------------------------------------------------
echo "5. Network"
PORT=$(grep -oP 'SOFTKVM_PORT="\K[0-9]+' "${UNIT_PATH}" 2>/dev/null | head -1)
PORT=${PORT:-3699}
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PORT}"; then
  ok "something is listening on port ${PORT}"
  ss -ltnp 2>/dev/null | grep ":${PORT}" | sed 's/^/        /'
elif command -v netstat >/dev/null 2>&1 && netstat -ltn 2>/dev/null | grep -q ":${PORT}"; then
  ok "something is listening on port ${PORT}"
else
  bad "nothing is listening on port ${PORT}"
fi

if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    ok "the app answers /healthz"
  else
    bad "the app does not answer /healthz on 127.0.0.1:${PORT}"
  fi
else
  warn "curl is not installed - skipping the HTTP check"
fi
echo

# --- 6. graphical session ------------------------------------------------
echo "6. Graphical session (needed for the screen stream)"
if [ -n "${DISPLAY:-}" ]; then
  ok "DISPLAY is set to ${DISPLAY}"
else
  warn "DISPLAY is not set in this shell"
  info "the dashboard and telemetry work either way, but the SCREEN STREAM"
  info "needs a real graphical session (see note below)."
fi
SESSIONS=$(loginctl list-sessions --no-legend 2>/dev/null | wc -l)
if [ "${SESSIONS:-0}" -gt 0 ]; then
  ok "${SESSIONS} login session(s) exist"
else
  warn "no login sessions exist - this looks like a headless machine"
  info "for a headless box the dashboard works but screen control cannot."
fi
if loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  ok "linger is enabled (survives logout)"
else
  warn "linger is not enabled - the service stops when you log out"
  info "fix: sudo loginctl enable-linger \$USER"
fi
echo

# --- 7. config -----------------------------------------------------------
echo "7. Configuration"
DATA_DIR=$(grep -oP 'SOFTKVM_DATA_DIR="\K[^"]+' "${UNIT_PATH}" 2>/dev/null | head -1)
DATA_DIR=${DATA_DIR:-${HOME}/.local/share/softkvm}
if [ -f "${DATA_DIR}/config.json" ]; then
  ok "config at ${DATA_DIR}/config.json"
  if grep -q '"password_hash"' "${DATA_DIR}/config.json"; then
    ok "an access password is set"
  else
    warn "no password is set yet - the first run will generate one"
  fi
else
  warn "no config.json yet at ${DATA_DIR}"
  info "it is created on the first start"
fi
if [ -f "${DATA_DIR}/softkvm.log" ]; then
  info "last log lines:"
  tail -n 8 "${DATA_DIR}/softkvm.log" | sed 's/^/        /'
fi
echo

# --- summary -------------------------------------------------------------
echo "==================="
printf '  passed: %d   failed: %d   warnings: %d\n' "${PASS}" "${FAIL}" "${WARN}"
echo

if [ "${FAIL}" -gt 0 ]; then
  echo "  Next steps:"
  echo "    1. ./deploy/install-linux.sh"
  echo "    2. systemctl --user restart ${SERVICE_NAME}"
  echo "    3. journalctl --user -u ${SERVICE_NAME} -n 40 --no-pager"
  echo
fi

echo "  Useful commands:"
echo "    systemctl --user status ${SERVICE_NAME}"
echo "    systemctl --user restart ${SERVICE_NAME}"
echo "    journalctl --user -u ${SERVICE_NAME} -f"
echo "    cd ${PROJECT_DIR} && .venv/bin/python server.py --reset-password"
echo
