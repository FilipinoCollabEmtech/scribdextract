"""Tiny backend bridge: web UI -> existing scribdextract.py pipeline.

Run locally (Option B: PC is the server, Netlify hosts only the static UI):
    pip install flask waitress
    set ALLOWED_ORIGINS=https://your-site.netlify.app
    python server.py          # -> http://0.0.0.0:8000 (LAN + tunnel reachable)

Expose it with free HTTPS so the Netlify (https) page can reach it:
    cloudflared tunnel --url http://localhost:8000
    # -> https://<name>.trycloudflare.com ; open your Netlify URL as
    #    https://your-site.netlify.app/?api=https://<name>.trycloudflare.com

Env:
    PORT (default 8000), HOST (default 0.0.0.0),
    ALLOWED_ORIGINS (comma list, default "*").

API:
    POST /api/extract {url} -> {id}
    GET  /api/status/<id>    -> {state, done, message, pages, files}
Files served from /files/<job>/<name> (absolute URLs when split-hosted).
"""
import os, re, threading, uuid
from flask import Flask, jsonify, request, send_from_directory, send_file

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)  # ScribdExtract/ (scribdextract.py lives here)
import sys
sys.path.insert(0, ROOT)
import scribdextract  # noqa: E402

app = Flask(__name__, static_folder=BASE, static_url_path="")
JOBS = {}
FILES_ROOT = os.path.join(BASE, "jobs")
os.makedirs(FILES_ROOT, exist_ok=True)

# Comma-separated origins allowed to call the API from a browser
# (your Netlify URL). "*" is fine for personal use behind a tunnel.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in
                   os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]


@app.before_request
def _cors_preflight():
    if request.method == "OPTIONS":
        return ("", 204)


@app.after_request
def _cors_headers(resp):
    origin = request.headers.get("Origin", "")
    if "*" in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    elif origin and origin.rstrip("/") in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.get("/")
def index():
    return send_file(os.path.join(BASE, "index.html"))


def _run_job(jid, url, name):
    j = JOBS[jid]
    try:
        key, _ = scribdextract.resolve(url, name)
        steps = list(scribdextract.STEPS)
        import scribd_basics, decode, build_text_pdf, download_pdf, render_pages
        fns = {"fetch": scribd_basics.main, "decode": decode.main,
               "text": build_text_pdf.main, "download": download_pdf.main,
               "render": render_pages.main}
        for i, st in enumerate(steps):
            j["message"] = st.capitalize() + "…"
            fns[st](key)
            j["done"] = i + 1
        import doc_config
        cfg = doc_config.DOCS[key]
        out = doc_config.out_dir(key)
        base = cfg["pdf_base"]
        # copy outputs into job dir for download
        import shutil
        dest = os.path.join(FILES_ROOT, jid)
        os.makedirs(dest, exist_ok=True)
        files = {}
        for src, label in ((base + ".pdf", "pdf"), (base + "-text.pdf", "textPdf"),
                           (base + "-full.pdf", "fullPdf"), ("decoded.txt", "txt")):
            p = os.path.join(out, src)
            if os.path.exists(p):
                shutil.copy(p, os.path.join(dest, src))
                # store site-relative path; status() expands to absolute
                # so split-hosting (Netlify UI + tunnel API) downloads work
                files[label] = f"/files/{jid}/{src}"
        # page count from decoded.txt
        pages = 0
        try:
            txt = open(os.path.join(dest, "decoded.txt"), encoding="utf-8").read()
            pages = len(re.findall(r"===== PAGE", txt))
        except OSError:
            pass
        j.update(state="done", message="Done", pages=pages, files=files)
    except Exception as e:  # noqa: BLE001
        j.update(state="error", message=f"{e}")


@app.post("/api/extract")
def extract():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if "scribd.com/document" not in url:
        return jsonify(error="not a scribd document URL"), 400
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"state": "run", "done": 0, "message": "Starting…"}
    name = re.sub(r"\s+", " ", url.split("/document/")[-1][:40]) or jid
    threading.Thread(target=_run_job, args=(jid, url, name), daemon=True).start()
    return jsonify(id=jid)


@app.get("/api/status/<jid>")
def status(jid):
    j = JOBS.get(jid)
    if not j:
        return jsonify(state="error", message="unknown job"), 404
    base = request.host_url.rstrip("/")
    files = {k: (v if v.startswith("http") else base + v)
             for k, v in (j.get("files") or {}).items()}
    return jsonify(state=j["state"], done=j["done"], message=j.get("message", ""),
                   pages=j.get("pages", 0), files=files)


@app.get("/files/<jid>/<path:name>")
def files(jid, name):
    return send_from_directory(os.path.join(FILES_ROOT, jid), name, as_attachment=True)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"serving {BASE} on http://{host}:{port}")
    print(f"CORS origins: {ALLOWED_ORIGINS}")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=True, threaded=True)
