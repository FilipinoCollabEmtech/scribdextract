"""ScribdExtract background runner (Windows).

Starts the Flask backend (server.py) + ngrok static-domain tunnel with NO
console windows, restarts either on crash, and logs to %TEMP%.

Usage:
    ScribdExtractBG.exe                    run in background (log only)
    ScribdExtractBG.exe --install-startup  auto-start on Windows login
    ScribdExtractBG.exe --remove-startup   remove auto-start

Env overrides: SCRIBD_NGROK_DOMAIN, SCRIBD_PORT, SCRIBD_ORIGINS
Requires once: ngrok logged in (ngrok config add-authtoken ...) and
    pip install flask waitress (server deps).
"""
import glob
import os
import shutil
import socket
import subprocess
import sys
import time

DEFAULT_DOMAIN = "lettuce-collision-italicize.ngrok-free.dev"
PORT = int(os.environ.get("SCRIBD_PORT", "8000"))
DOMAIN = os.environ.get("SCRIBD_NGROK_DOMAIN", DEFAULT_DOMAIN)
HERE = os.path.dirname(os.path.abspath(__file__))


def project_dir():
    """Folder containing server.py. One-file exes unpack to a temp dir,
    so prefer the exe's own folder, then the script folder, then cwd."""
    cands = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(exe_dir)  # exe copied next to server.py
        cands.append(os.path.dirname(exe_dir))  # exe in dist/ subfolder
    cands += [HERE, os.getcwd()]
    for c in cands:
        if os.path.exists(os.path.join(c, "server.py")):
            return c
    return cands[0]


BASE = project_dir()
SERVER_PY = os.path.join(BASE, "server.py")
LOG = os.path.join(os.environ.get("TEMP", HERE), "scribdextract_bg.log")

# No console windows for children.
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def find_ngrok():
    p = shutil.which("ngrok")
    if p:
        return p
    cands = []
    for pat in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.*\ngrok.exe"),
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\Ngrok.*\ngrok.exe",
        r"C:\Program Files*\ngrok\ngrok.exe",
    ):
        cands.extend(glob.glob(pat))
    return cands[0] if cands else None


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def find_python():
    cands = [shutil.which("python")]
    cands += [os.path.join(p, "python.exe") for p in
              (r"C:\Python314", sys.base_prefix, sys.exec_prefix)]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def run_backend():
    """Start server.py with SYSTEM python (frozen exe bundles no deps and
    sys.executable IS this exe, so it must never spawn itself)."""
    import threading  # noqa: F401  (kept for future in-process option)
    py = find_python()
    if not py:
        log("ERROR: python.exe not found (need system Python + Flask deps)")
        return None
    env = dict(os.environ)
    env.setdefault("ALLOWED_ORIGINS", "*")
    env.setdefault("PORT", str(PORT))
    env.setdefault("HOST", "0.0.0.0")
    log(f"starting server: {py} {SERVER_PY}")
    try:
        return subprocess.Popen(
            [py, SERVER_PY],
            cwd=BASE, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW)
    except OSError as e:
        log(f"server start failed: {e}")
        return None


def start_ngrok(exe):
    log(f"starting tunnel: {exe} http --domain={DOMAIN} {PORT}")
    return subprocess.Popen(
        [exe, "http", f"--domain={DOMAIN}", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        creationflags=_NO_WINDOW)


def startup_link():
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "ScribdExtractBG.lnk")
    return startup


def install_startup(exe_path):
    link = startup_link()
    ps = (f"$s=New-Object -ComObject WScript.Shell;"
          f"$l=$s.CreateShortcut('{link}');"
          f"$l.TargetPath='{exe_path}';$l.WorkingDirectory='{BASE}';$l.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    print("installed auto-start: " + link if r.returncode == 0 else r.stderr)
    return r.returncode == 0


def main(argv):
    me = os.path.abspath(sys.argv[0])
    if "--install-startup" in argv:
        ok = install_startup(me)
        return 0 if ok else 1
    if "--remove-startup" in argv:
        try:
            os.remove(startup_link())
            print("removed auto-start")
        except OSError as e:
            print(e)
        return 0

    ngrok_exe = find_ngrok()
    if not ngrok_exe:
        log("ERROR: ngrok.exe not found (install ngrok + add authtoken first)")
        return 1
    if not os.path.exists(SERVER_PY):
        log(f"ERROR: missing {SERVER_PY}")
        return 1

    os.environ.setdefault("ALLOWED_ORIGINS", "*")
    os.environ.setdefault("PORT", str(PORT))
    os.environ.setdefault("HOST", "0.0.0.0")
    server = run_backend()
    tunnel = start_ngrok(ngrok_exe)
    log(f"running (server pid={server.pid if server else None}, "
        f"ngrok pid={tunnel.pid})")
    ngrok_cooldown_until = 0.0
    try:
        while True:
            time.sleep(10)
            if server is None or server.poll() is not None:
                log("backend down, restarting: "
                    f"{getattr(server, 'returncode', None)}")
                server = run_backend()
            if tunnel.poll() is not None:
                # back off: a busy static domain exits immediately and
                # would otherwise hot-loop (e.g. tunnel already open
                # in a terminal).
                wait = max(0.0, ngrok_cooldown_until - time.time())
                if wait:
                    time.sleep(wait)
                log(f"ngrok exited ({tunnel.returncode}), restarting")
                tunnel = start_ngrok(ngrok_exe)
                ngrok_cooldown_until = time.time() + 30
    except KeyboardInterrupt:
        pass
    finally:
        for p in (server, tunnel):
            try:
                if p is not None:
                    p.terminate()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
