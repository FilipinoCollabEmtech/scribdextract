/* ScribdExtract Web frontend
 * Flow: submit → rewarded inline ad → POST /api/extract → poll → downloads
 * NO popups: never window.open. Ads render inline in .ad-box / #rewarded-ad-slot.
 *
 * Backend URL resolves as: ?api= query param > localStorage >
 *   window.SCRIBD_API_BASE > baked-in default (ngrok static domain) > same origin.
 *   Visitors just open the plain Netlify URL; ?api= is admin override only.
 */
const API_BASE = (() => {
  const clean = u => (u || "").trim().replace(/\/+$/, "");
  try {
    const q = new URLSearchParams(location.search).get("api");
    if (q) { try { localStorage.setItem("scribd_api", clean(q)); } catch (_) {} return clean(q); }
    const s = localStorage.getItem("scribd_api");
    if (s) return clean(s);
  } catch (_) {}
  return clean(window.SCRIBD_API_BASE || "https://lettuce-collision-italicize.ngrok-free.dev");
})();
window.getScribdApi = () => API_BASE;
window.setScribdApi = (u) => {
  try { localStorage.setItem("scribd_api", (u || "").trim().replace(/\/+$/, "")); } catch (_) {}
  location.reload();
};
const apiUrl = (p) => API_BASE + p;
// File hrefs may be absolute (split-host) or site-relative (same-origin).
const absUrl = (h) => !h ? "#" : (/^https?:\/\//.test(h) ? h : (API_BASE || "") + h);
const REWARD_SECONDS = 5; // match your ad network's rewarded duration

const form = document.getElementById("extract-form");
const urlInput = document.getElementById("scribd-url");
const badge = document.getElementById("status-badge");
const msg = document.getElementById("status-msg");
const fill = document.getElementById("progress-fill");
const results = document.getElementById("results");
const stepsEl = document.getElementById("steps-list");

const modal = document.getElementById("reward-modal");
const playBtn = document.getElementById("reward-play");
const cancelBtn = document.getElementById("reward-cancel");
const rewardFill = document.getElementById("reward-fill");
const rewardCount = document.getElementById("reward-count");

let pendingUrl = "";
let rewardTimer = null;

document.querySelectorAll("[data-example]").forEach(b =>
  b.addEventListener("click", () => { urlInput.value = b.dataset.example; urlInput.focus(); })
);

form.addEventListener("submit", e => {
  e.preventDefault();
  const u = urlInput.value.trim();
  if (!/^https:\/\/www\.scribd\.com\/document\//.test(u)) {
    setStatus("error", "Error", "Please paste a valid public Scribd document URL.");
    return;
  }
  pendingUrl = u;
  openReward(); // gate every extract behind one inline rewarded view
});

/* ── Rewarded ad (inline) ── */
function openReward() {
  modal.classList.remove("hidden");
  playBtn.disabled = false;
  playBtn.textContent = "▶ Play ad";
  rewardFill.style.width = "0";
  rewardCount.textContent = `Reward in 0:0${REWARD_SECONDS}`;
}
function closeReward() {
  modal.classList.add("hidden");
  clearInterval(rewardTimer);
}

cancelBtn.addEventListener("click", closeReward);
modal.addEventListener("click", e => { if (e.target === modal) closeReward(); });

playBtn.addEventListener("click", () => {
  playBtn.disabled = true;
  playBtn.textContent = "Playing…";
  simulateRewardedAd(REWARD_SECONDS, () => {
    closeReward();
    runExtraction(pendingUrl); // reward earned
  });
});

/* Replace this stub with your real rewarded unit.
 * Example (AdSense rewarded / custom VAST): render your creative into
 * #rewarded-ad-slot, resolve on reward callback, reject on skip/close. */
function simulateRewardedAd(seconds, onReward) {
  // TODO: paste real tag, e.g.:
  //   window.myAdNetwork.showRewarded(document.getElementById('rewarded-ad-slot'))
  //     .then(onReward).catch(closeReward);
  let t = 0;
  clearInterval(rewardTimer);
  rewardTimer = setInterval(() => {
    t += 0.1;
    const p = Math.min(100, (t / seconds) * 100);
    rewardFill.style.width = p + "%";
    rewardCount.textContent = `Reward in 0:0${Math.max(0, Math.ceil(seconds - t))}`;
    if (t >= seconds) { clearInterval(rewardTimer); onReward(); }
  }, 100);
}

/* ── Extraction ── */
function setStatus(cls, label, text) {
  badge.className = "status-badge " + (cls || "");
  badge.textContent = label;
  if (text) msg.textContent = text;
}
function markStep(name, state) {
  const li = stepsEl.querySelector(`[data-step="${name}"]`);
  if (li) li.className = state; // "", "active", "done"
}
const STEP_ORDER = ["fetch", "decode", "text", "download", "render"];

async function runExtraction(url) {
  setStatus("busy", "Working", "Starting extraction…");
  results.classList.add("hidden");
  STEP_ORDER.forEach(s => markStep(s, ""));
  fill.style.width = "4%";
  try {
    // Try backend first, fall back to demo mode (offline preview).
    const res = await fetch(apiUrl("/api/extract"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    if (!res.ok) throw new Error("backend " + res.status);
    const job = await res.json();
    await pollJob(job.id);
  } catch (err) {
    console.warn("backend unavailable, demo mode:", err);
    const onNetlify = !/^(localhost|127\.0\.0\.1|0\.0\.0\.0)/.test(location.hostname);
    if (onNetlify && !API_BASE) {
      setStatus("error", "No backend",
        "This page is on Netlify but no backend is set. Open it once as ?api=YOUR_TUNNEL_URL — e.g. "
        + location.origin + location.pathname + "?api=https://<name>.trycloudflare.com");
      return;
    }
    await demoProgress();
  }
}

async function pollJob(id) {
  for (;;) {
    const r = await fetch(apiUrl(`/api/status/${id}`));
    const j = await r.json();
    STEP_ORDER.forEach((s, i) => {
      markStep(s, i < j.done ? "done" : (i === j.done ? "active" : ""));
    });
    fill.style.width = (8 + (j.done / STEP_ORDER.length) * 92) + "%";
    setStatus("busy", "Working", j.message || "Working…");
    if (j.state === "done") {
      STEP_ORDER.forEach(s => markStep(s, "done"));
      fill.style.width = "100%";
      setStatus("done", "Done", `Extracted ${j.pages} pages.`);
      wireResults(j.files || {});
      results.classList.remove("hidden");
      return;
    }
    if (j.state === "error") { setStatus("error", "Error", j.message); return; }
    await new Promise(r => setTimeout(r, 1200));
  }
}

function wireResults(files) {
  const map = { "dl-pdf": files.pdf, "dl-text": files.textPdf, "dl-full": files.fullPdf, "dl-txt": files.txt };
  for (const [id, href] of Object.entries(map)) {
    const a = document.getElementById(id);
    if (href) { a.href = absUrl(href); a.removeAttribute("aria-disabled"); }
    else { a.href = "#"; a.setAttribute("aria-disabled", "true"); }
  }
  if (API_BASE) {
    try {
      const host = new URL(API_BASE).host;
      msg.textContent += ` (via ${host})`;
    } catch (_) {}
  }
}

// Offline preview so the page (and ad flow) is testable without Python running.
async function demoProgress() {
  const msgs = ["Fetching pages…", "Decoding text…", "Building text PDF…", "Downloading slices…", "Rendering full PDF…"];
  for (let i = 0; i < STEP_ORDER.length; i++) {
    markStep(STEP_ORDER[i], "active");
    setStatus("busy", "Working", msgs[i] + " (demo — start server.py for real files)");
    fill.style.width = (8 + ((i + 1) / STEP_ORDER.length) * 92) + "%";
    await new Promise(r => setTimeout(r, 900));
    markStep(STEP_ORDER[i], "done");
  }
  setStatus("done", "Demo done", "Backend not connected. Run server.py to get real downloads.");
}
