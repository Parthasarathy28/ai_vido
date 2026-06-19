const $ = (id) => document.getElementById(id);

const STAGES = [
  ["script", "Script"],
  ["voice", "Voice"],
  ["images", "Visuals"],
  ["captions", "Subtitles"],
  ["render", "Render"],
  ["upload", "Upload"],
];

// ---------- capabilities banner ----------
async function loadInfo() {
  const r = await fetch("/api/info");
  const info = await r.json();
  const pills = [
    ["GPU", info.gpu],
    ["OpenAI", info.openai],
    ["Claude", info.anthropic],
    ["DALL·E/SD", info.diffusers || info.openai],
    ["Pexels", info.pexels],
    ["YouTube", info.youtube_enabled],
  ];
  $("caps").innerHTML = pills
    .map(([name, on]) => `<span class="pill ${on ? "on" : ""}">${name}: ${on ? "on" : "off"}</span>`)
    .join("");
  // Only show the upload checkbox if YouTube is configured.
  $("uploadWrap").style.display = info.youtube_enabled ? "inline-flex" : "none";
}

// ---------- segmented controls ----------
function setupSeg(id, initial) {
  const seg = $(id);
  let value = initial;
  seg.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      value = b.dataset.v;
    });
  });
  return () => value;
}
const getMode = setupSeg("modeSeg", "auto");
const getOrient = setupSeg("orientSeg", "horizontal");

// ---------- language selector (built from API) ----------
let getLang = () => "en";
let LANGS = [];
async function loadLanguages() {
  const r = await fetch("/api/languages");
  LANGS = await r.json();
  const seg = $("langSeg");
  seg.innerHTML = LANGS.map((l, i) =>
    `<button data-v="${l.code}" class="${i === 0 ? "on" : ""}">${l.native}</button>`
  ).join("");
  getLang = setupSeg("langSeg", "en");
  // show a hint for languages without a free voice
  seg.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => updateLangHint(b.dataset.v));
  });
  updateLangHint("en");
}
function updateLangHint(code) {
  const l = LANGS.find((x) => x.code === code);
  const hint = $("langHint");
  if (!l) { hint.textContent = ""; return; }
  if (!l.voice_available) {
    hint.innerHTML = `⚠ ${l.name} needs a cloud voice key (<b>OpenAI</b> or ElevenLabs) ` +
      `for narration. Add OPENAI_API_KEY to your .env.`;
  } else if (!l.free_voice) {
    hint.innerHTML = `🔊 ${l.name} narration uses <b>OpenAI voice</b> (your OpenAI credit).`;
  } else {
    hint.textContent = "";
  }
}

// ---------- style selector ----------
let getStyle = () => "cartoon3d";
async function loadStyles() {
  const r = await fetch("/api/styles");
  const styles = await r.json();
  const seg = $("styleSeg");
  seg.innerHTML = styles.map((s, i) =>
    `<button data-v="${s.code}" class="${i === 0 ? "on" : ""}">${s.name}</button>`
  ).join("");
  getStyle = setupSeg("styleSeg", styles[0]?.code || "cartoon3d");
}

// ---------- generate + poll ----------
let pollTimer = null;

function renderLog(lines) {
  const box = $("logBox");
  if (!lines || !lines.length) { box.hidden = true; return; }
  box.hidden = false;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent = lines.join("\n");
  if (atBottom) box.scrollTop = box.scrollHeight;  // autoscroll unless user scrolled up
}

function renderStages(currentStage, status) {
  const curIdx = STAGES.findIndex(([k]) => k === currentStage);
  $("stages").innerHTML = STAGES.map(([k, label], i) => {
    let cls = "step";
    if (status === "done" || i < curIdx) cls += " done";
    else if (i === curIdx) cls += " active";
    return `<span class="${cls}">${label}</span>`;
  }).join("");
}

// We track a SET of active jobs so you can queue several stories at once.
// The progress panel always reflects the most recently started running job.
function activeJobs() {
  try { return JSON.parse(localStorage.getItem("activeJobs") || "[]"); }
  catch { return []; }
}
function setActiveJobs(ids) { localStorage.setItem("activeJobs", JSON.stringify(ids)); }
function addActiveJob(id) {
  const ids = activeJobs(); if (!ids.includes(id)) ids.push(id); setActiveJobs(ids);
}
function removeActiveJob(id) { setActiveJobs(activeJobs().filter((x) => x !== id)); }

let focusedJob = null;   // job shown in the progress panel

async function startGenerate() {
  const prompt = $("prompt").value.trim();
  if (!prompt) { $("prompt").focus(); return; }

  $("result").hidden = true;
  $("errorBox").hidden = true;
  $("progress").hidden = false;
  $("barFill").style.width = "3%";
  $("statusText").textContent = "Starting…";
  renderStages("script", "start");

  const r = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      upload: $("upload").checked,
      mode: getMode(),
      orientation: getOrient(),
      language: getLang(),
      style: getStyle(),
    }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    showError(e.detail || "Failed to start.");
    return;
  }
  const job = await r.json();
  addActiveJob(job.id);
  focusedJob = job.id;
  $("prompt").value = "";                 // clear so you can type the next story
  $("prompt").focus();
  ensurePolling();
}

// One timer polls every active job. The form stays enabled the whole time, so
// you can submit 4-5 stories that all generate (queued by the server).
function ensurePolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollAll, 1200);
  pollAll();
}

async function pollAll() {
  const ids = activeJobs();
  if (!ids.length) { clearInterval(pollTimer); pollTimer = null; return; }
  let running = 0;
  for (const id of ids) {
    const r = await fetch(`/api/jobs/${id}`);
    if (r.status === 404) { removeActiveJob(id); continue; }
    if (!r.ok) continue;
    const job = await r.json();

    if (!focusedJob || focusedJob === id) {
      focusedJob = id;
      $("progress").hidden = false;
      $("barFill").style.width = Math.max(3, job.progress * 100) + "%";
      renderStages(job.stage, job.status === "done" ? "done" : "start");
      $("cancelBtn").hidden = !(job.status === "running" || job.status === "queued");
      $("cancelBtn").dataset.id = id;
      const queued = ids.length - 1;
      const pct = Math.round(job.progress * 100);
      const pctTxt = (job.status === "running" || job.status === "queued")
        ? ` ${pct}%` : "";
      $("statusText").textContent =
        (job.status === "running" ? `${job.stage_label}…${pctTxt}` :
         job.status === "done" ? "Done! 100%" :
         job.status === "cancelled" ? "Stopped." :
         job.status === "error" ? "Error" : `Queued…`) +
        (queued > 0 ? `   (+${queued} more in queue)` : "");
      renderLog(job.log);
      if (job.status === "done") { showResult(job); }
      else if (job.status === "error") { showError(job.error || "Failed."); }
    }

    if (["done", "error", "cancelled"].includes(job.status)) {
      removeActiveJob(id);
      if (focusedJob === id) focusedJob = null;
      loadLibrary();
    } else {
      running++;
    }
  }
  if (!activeJobs().length) { clearInterval(pollTimer); pollTimer = null; $("cancelBtn").hidden = true; }
}

async function cancelJob(id) {
  await fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).catch(() => {});
  $("cancelBtn").textContent = "Stopping…";
}

// Re-attach to any jobs that were running when the page was refreshed.
function resumeActiveJob() {
  if (activeJobs().length) ensurePolling();
}

function showResult(job) {
  if (!job.video_url) return;
  $("result").hidden = false;
  $("player").src = job.video_url;
  $("download").href = job.video_url;
  if (job.youtube_id) {
    $("ytLink").hidden = false;
    $("ytLink").href = `https://youtu.be/${job.youtube_id}`;
  } else {
    $("ytLink").hidden = true;
  }
  const c = job.cost;
  const box = $("costBox");
  if (c && c.total_usd != null) {
    box.hidden = false;
    box.innerHTML = `💰 OpenAI cost ≈ <b>$${c.total_usd.toFixed(3)}</b> ` +
      `<span class="muted">(script $${(c.script_usd||0).toFixed(3)} · ` +
      `images $${(c.image_usd||0).toFixed(3)} [${c.image_count||0}] · ` +
      `voice $${(c.voice_usd||0).toFixed(3)})</span>`;
  } else {
    box.hidden = true;
  }
}

function showError(msg) {
  $("progress").hidden = true;
  $("errorBox").hidden = false;
  $("errorBox").textContent = "⚠ " + msg;
}

// ---------- library ----------
async function loadLibrary() {
  const r = await fetch("/api/library");
  const items = await r.json();
  if (!items.length) {
    $("gallery").innerHTML = `<p class="empty">No videos yet. Generate your first one above.</p>`;
    return;
  }
  const langOpts = LANGS.map((l) =>
    `<option value="${l.code}">${l.native}${l.free_voice ? "" : " (key)"}</option>`
  ).join("");
  $("gallery").innerHTML = items.map((it) => {
    const media = it.has_video
      ? `<video src="${it.video_url}" controls preload="metadata"></video>`
      : `<div class="meta empty" style="padding:24px">No video file</div>`;
    const langRow = it.has_video ? `
      <div class="langRow">
        <select class="langPick" data-id="${it.id}">${langOpts}</select>
        <button class="btn-ghost small playLang" data-id="${it.id}">🌐 Play in language</button>
        <button class="btn-ghost small dlLang" data-id="${it.id}">⬇</button>
      </div>
      <div class="langStatus" data-id="${it.id}"></div>` : "";
    const cost = it.cost && it.cost.total_usd != null
      ? `<div class="tileCost">💰 ≈ $${it.cost.total_usd.toFixed(3)}</div>` : "";
    return `<div class="tile">
      ${media}
      <div class="meta">
        <div class="t">${escapeHtml(it.title || it.prompt)}</div>
        <div class="p">${escapeHtml(it.prompt)}</div>
        ${cost}
        ${langRow}
        <button class="btn-ghost small delProj" data-id="${it.id}"
          data-title="${escapeHtml(it.title || it.prompt)}">🗑 Delete</button>
      </div>
    </div>`;
  }).join("");

  document.querySelectorAll(".delProj").forEach((b) => {
    b.addEventListener("click", () => deleteProject(b.dataset.id, b.dataset.title));
  });

  // "Play in language": swap the tile's player to that language (make it first
  // if needed). "⬇" downloads that language version.
  document.querySelectorAll(".playLang").forEach((b) => {
    b.addEventListener("click", () => {
      const id = b.dataset.id;
      const sel = document.querySelector(`.langPick[data-id="${id}"]`);
      playInLanguage(id, sel.value, b);
    });
  });
  document.querySelectorAll(".dlLang").forEach((b) => {
    b.addEventListener("click", () => {
      const id = b.dataset.id;
      const sel = document.querySelector(`.langPick[data-id="${id}"]`);
      makeLanguageVersion(id, sel.value, b, /*download=*/true);
    });
  });
}

// Swap the tile's <video> to the chosen language, generating it if needed.
async function playInLanguage(projectId, language, btn) {
  const status = document.querySelector(`.langStatus[data-id="${projectId}"]`);
  const tile = btn.closest(".tile");
  const video = tile.querySelector("video");

  // 1. Already have this language version? Swap immediately.
  const vr = await fetch(`/api/projects/${projectId}/versions`);
  if (vr.ok) {
    const data = await vr.json();
    const match = data.versions.find((v) => v.language === language);
    if (match) {
      video.src = match.video_url;
      video.play().catch(() => {});
      status.textContent = `▶ Playing in ${language}`;
      return;
    }
  }

  // 2. Not yet — generate it, then swap the player to it.
  const url = await makeLanguageVersion(projectId, language, btn, /*download=*/false);
  if (url) {
    video.src = url;
    video.play().catch(() => {});
    status.textContent = `▶ Playing in ${language}`;
  }
}

// Generate a language version. Returns its video URL (or null). Optionally
// downloads it when done.
async function makeLanguageVersion(projectId, language, btn, download) {
  const status = document.querySelector(`.langStatus[data-id="${projectId}"]`);
  btn.disabled = true;
  status.textContent = "Preparing language version…";
  const r = await fetch(`/api/projects/${projectId}/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    status.textContent = "⚠ " + (e.detail || "Failed");
    btn.disabled = false;
    return null;
  }
  const job = await r.json();
  return await new Promise((resolve) => {
    const timer = setInterval(async () => {
      const jr = await fetch(`/api/jobs/${job.id}`);
      if (!jr.ok) return;
      const j = await jr.json();
      status.textContent =
        j.status === "running" ? `${j.stage_label}…` :
        j.status === "done" ? "✅ Ready" :
        j.status === "error" ? "⚠ " + j.error : "Queued…";
      if (j.status === "done") {
        clearInterval(timer);
        btn.disabled = false;
        if (download) {
          const a = document.createElement("a");
          a.href = j.video_url;
          a.download = `${projectId}-${language}.mp4`;
          document.body.appendChild(a); a.click(); a.remove();
        }
        loadLibrary();
        resolve(j.video_url);
      } else if (j.status === "error") {
        clearInterval(timer);
        btn.disabled = false;
        resolve(null);
      }
    }, 1500);
  });
}

async function deleteProject(id, title) {
  if (!confirm(`Delete "${title}"? This removes the video and its files.`)) return;
  const r = await fetch(`/api/projects/${id}`, { method: "DELETE" });
  if (r.ok) {
    loadLibrary();
  } else {
    const e = await r.json().catch(() => ({}));
    alert("Could not delete: " + (e.detail || r.status));
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- wire up ----------
// Only one video plays at a time: when any starts, pause all the others.
document.addEventListener("play", (e) => {
  if (e.target.tagName !== "VIDEO") return;
  document.querySelectorAll("video").forEach((v) => {
    if (v !== e.target) v.pause();
  });
}, true);  // capture phase so it catches all video elements, incl. dynamic ones

$("go").addEventListener("click", startGenerate);
$("cancelBtn").addEventListener("click", () => {
  const id = $("cancelBtn").dataset.id;
  if (id) cancelJob(id);
});
$("refresh").addEventListener("click", loadLibrary);
loadInfo();
loadLanguages();
loadStyles();
loadLibrary();
resumeActiveJob();
