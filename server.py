"""Tiny backend bridge: web UI -> existing scribdextract.py pipeline.

Run:
    pip install flask
    python server.py          # -> http://localhost:8000 (serves web-extract/ + API)

API:
    POST /api/extract {url} -> {id}
    GET  /api/status/<id>    -> {state, done, message, pages, files}
Files served from /files/<job>/<name>.
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
    return jsonify(state=j["state"], done=j["done"], message=j.get("message", ""),
                   pages=j.get("pages", 0), files=j.get("files", {}))


@app.get("/files/<jid>/<path:name>")
def files(jid, name):
    return send_from_directory(os.path.join(FILES_ROOT, jid), name, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
