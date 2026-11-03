"""Verify that a password reset from another process takes effect live."""

from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

PORT = 3799
BASE = f"http://127.0.0.1:{PORT}"
DATA_DIR = r"C:\Users\Qiyam Ahmadzai\AppData\Local\Temp\softkvm_livetest2"


def cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "server.py", *args],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "SOFTKVM_DATA_DIR": DATA_DIR},
        cwd=r"D:\myPythonProjects\open-soft-kvm",
    )
    return result.stdout


def login(password: str) -> int:
    """Return the HTTP status of a login attempt (no redirect following)."""
    jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        NoRedirect(),
    )
    data = f"password={password}".encode()
    request = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
    try:
        with opener.open(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    import os

    os.makedirs(DATA_DIR, exist_ok=True)
    env = {**os.environ, "SOFTKVM_DATA_DIR": DATA_DIR}
    os.environ.update(env)

    subprocess.run(
        [sys.executable, "server.py", "--set-password", "first-password-1"],
        capture_output=True, text=True, env=env, cwd=r"D:\myPythonProjects\open-soft-kvm",
    )

    server = subprocess.Popen(
        [sys.executable, "server.py"],
        env={**env, "SOFTKVM_PORT": str(PORT)},
        cwd=r"D:\myPythonProjects\open-soft-kvm",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        # wait for the server to accept connections
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"{BASE}/healthz", timeout=3):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("server never came up")
            return 1

        print(f"login with 'first-password-1' -> {login('first-password-1')}   (expect 302)")
        print(f"login with 'wrong-password'   -> {login('wrong-password')}   (expect 401)")

        print("\n>>> resetting the password from a separate process (no restart)...")
        output = cli("--reset-password")
        match = re.search(r"^\s{4}(\S+)\s*$", output, re.MULTILINE)
        new_password = match.group(1) if match else None
        print(f"    new password: {new_password}")
        if not new_password:
            print("could not parse the new password")
            return 1

        time.sleep(0.5)
        print(f"login with the NEW password   -> {login(new_password)}   (expect 302)")
        print(f"login with the OLD password   -> {login('first-password-1')}   (expect 401)")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
