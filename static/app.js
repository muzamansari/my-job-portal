const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const STATUS_COLORS = { Saved: "#3fb6f2", Applied: "#1d3fa6", Interview: "#d99a14", Offer: "#16a34a", Rejected: "#94a3b8" };
const LOGO_COLORS = [
  "linear-gradient(135deg,#8fdcff,#2a8fe0 50%,#1d3fa6)", "linear-gradient(135deg,#5b7be0,#1d3fa6 50%,#0f2460)",
  "linear-gradient(135deg,#fff1b8,#e8b93e 50%,#b8861b)", "linear-gradient(135deg,#b9e7ff,#3fb6f2 50%,#1f86c9)",
  "linear-gradient(135deg,#86efac,#16a34a 50%,#14532d)", "linear-gradient(135deg,#c4b5fd,#6d5ae6 50%,#3b2a9e)",
];
let resumeText = "";
let STATUSES = Object.keys(STATUS_COLORS);
let apps = [];

// ---------- helpers ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function scoreColor(v) {
  if (v >= 70) return "#16a34a";
  if (v >= 50) return "#1d3fa6";
  if (v >= 25) return "#d99a14";
  return "#dc2626";
}
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Something went wrong.");
  return data;
}
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), error ? 6000 : 3000);
}
function loading(on, text = "Working…") {
  $("#loader").classList.toggle("hidden", !on);
  $("#loader-text").textContent = text;
}
async function busy(text, fn) {
  loading(true, text);
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { loading(false); }
}
function md(text) {
  // Small, safe Markdown renderer: headings, bullets, bold, italics.
  const lines = esc(text).split(/\r?\n/);
  let out = "", inList = false;
  const inline = s => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\W)\*(?!\s)(.+?)\*(?=\W|$)/g, "$1<i>$2</i>");
  for (const line of lines) {
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    if (bullet) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(bullet[1])}</li>`; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
    else if (line.trim()) out += `<p>${inline(line)}</p>`;
  }
  return out + (inList ? "</ul>" : "");
}
function download(name, content, type) {
  const blob = new Blob([content], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function wordDoc(title, bodyHtml) {
  return `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>body{font-family:Calibri,Arial,sans-serif;font-size:11pt;line-height:1.4}h1{font-size:20pt;color:#0d4f86;margin:0}h2{font-size:12pt;color:#1f7cc0;text-transform:uppercase;border-bottom:1px solid #a6d8f7;margin-top:14pt}h3{font-size:11pt}</style></head><body>${bodyHtml}</body></html>`;
}
function fileBase(app) {
  return [app.company, app.title].filter(Boolean).join(" - ").replace(/[\\/:*?"<>|]/g, "").slice(0, 80) || "job";
}

// ---------- navigation ----------
function show(view) {
  $$(".nav-item[data-view]").forEach(t => t.classList.toggle("active", t.dataset.view === view));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + view));
  if (view === "dashboard") loadDashboard();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$$(".nav-item[data-view]").forEach(t => t.addEventListener("click", () => show(t.dataset.view)));
$("#avatar").addEventListener("click", () => show("resume"));
$("#status-nav").addEventListener("click", e => {
  const item = e.target.closest("[data-status]");
  if (!item) return;
  $("#filter-status").value = item.dataset.status;
  if (!$("#view-dashboard").classList.contains("active")) show("dashboard"); else renderApps();
});

function resumeName(text) {
  const first = (text || "").split(/\r?\n/).map(l => l.replace(/[#*]/g, "").trim()).find(Boolean) || "";
  const name = first.split(/[|·,•–-]/)[0].trim();
  return name && name.length <= 40 && !/\d|@/.test(name) && name.split(/\s+/).length <= 4 ? name : "";
}
function initials(name) {
  return name ? name.split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase() : "JP";
}
function renderProfile() {
  const name = resumeName(resumeText);
  $("#hero-hello").textContent = name ? `Hi, ${name.split(/\s+/)[0]}!` : "Hi there!";
  $("#hero-date").textContent = "📅 " + new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  $("#avatar").textContent = $("#profile-avatar").textContent = initials(name);
  $("#profile-name").textContent = name || "Your profile";
  $("#profile-sub").textContent = resumeText ? "Resume saved ✓" : "Add your resume to begin";
}
document.addEventListener("click", e => { const g = e.target.closest("[data-goto]"); if (g) show(g.dataset.goto); });

// ---------- dashboard ----------
async function loadDashboard() {
  try {
    const [stats, list] = await Promise.all([api("/api/stats"), api("/api/applications")]);
    apps = list;
    STATUSES = stats.statuses;
    renderStats(stats);
    renderApps();
  } catch (e) { toast(e.message, true); }
}

function renderStats(s) {
  $("#k-total").textContent = s.total;
  $("#k-sent").textContent = s.sent;
  $("#k-interviews").textContent = s.by_status.Interview + s.by_status.Offer;
  $("#k-rate").textContent = s.interview_rate == null ? "Interviews" : `Interviews · ${s.interview_rate}% rate`;
  $("#k-offers").textContent = s.by_status.Offer;
  $("#k-fit").textContent = s.avg_fit == null ? "–" : s.avg_fit + "%";
  $("#k-chance").textContent = s.avg_chance == null ? "–" : s.avg_chance + "%";
  $("#fit-bar").style.width = (s.avg_fit || 0) + "%";
  $("#chance-bar").style.width = (s.avg_chance || 0) + "%";

  const steps = [
    [!!resumeText, "Resume added"],
    [s.total > 0, "First job analysed"],
    [apps.some(a => a.has_documents), "Tailored CV created"],
    [s.sent > 0, "First application sent"],
    [s.by_status.Interview + s.by_status.Offer > 0, "Interview landed"],
  ];
  $("#checklist").innerHTML = steps.map(([done, label]) => `<li class="${done ? "done" : ""}">${label}</li>`).join("");

  $("#status-nav").innerHTML = STATUSES.map(st =>
    `<button class="nav-item" data-status="${st}" style="--c:${STATUS_COLORS[st]}"><span class="dot"></span>${st}<span class="count">${s.by_status[st]}</span></button>`).join("");

  const total = s.total || 1;
  $("#pipeline").innerHTML = s.total
    ? STATUSES.filter(st => s.by_status[st]).map(st =>
        `<div style="width:${100 * s.by_status[st] / total}%;background:${STATUS_COLORS[st]}" title="${st}: ${s.by_status[st]}">${s.by_status[st]}</div>`).join("")
    : "";
  $("#pipeline-legend").innerHTML = STATUSES.map(st =>
    `<span><i style="background:${STATUS_COLORS[st]}"></i>${st}<b>${s.by_status[st]}</b></span>`).join("");

  const max = Math.max(1, ...s.weeks.map(w => w.count));
  $("#weeks").innerHTML = s.weeks.map(w => {
    const d = new Date(w.week + "T00:00");
    const label = d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    return `<div class="wk"><span class="n">${w.count || ""}</span><div class="bar" style="height:${(w.count / max) * 100}%"></div><span class="lbl">${label}</span></div>`;
  }).join("");

  const sel = $("#filter-status");
  if (sel.options.length === 1) STATUSES.forEach(st => sel.add(new Option(st, st)));
}

function scoreBlock(label, v) {
  return `<div><label>${label}<b>${v == null ? "–" : v + "%"}</b></label><div class="mini-bar"><i style="width:${v || 0}%;background:${scoreColor(v || 0)}"></i></div></div>`;
}
function logoColor(text) {
  let h = 0;
  for (const c of text || "?") h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return LOGO_COLORS[h % LOGO_COLORS.length];
}
function ago(ts) {
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  return days <= 0 ? "Added today" : days === 1 ? "Added yesterday" : `Added ${days} days ago`;
}
function statusSelect(app) {
  return `<select class="status-select" data-id="${app.id}" style="--c:${STATUS_COLORS[app.status]}">` +
    STATUSES.map(st => `<option ${st === app.status ? "selected" : ""}>${st}</option>`).join("") + "</select>";
}

function renderApps() {
  const q = $("#search").value.trim().toLowerCase();
  const st = $("#filter-status").value;
  const rows = apps.filter(a => (!st || a.status === st) &&
    (!q || (a.title + " " + a.company).toLowerCase().includes(q)));
  $("#apps-count").textContent = rows.length;
  $("#apps-grid").innerHTML = rows.map(a => `
    <article class="job" style="--c:${STATUS_COLORS[a.status]}">
      <div class="job-top" data-open="${a.id}">
        <div class="job-logo" style="background:${logoColor(a.company || a.title)}">${esc((a.company || a.title || "?").trim()[0] || "?").toUpperCase()}</div>
        <div>
          <div class="job-title">${esc(a.title || "Untitled role")}</div>
          <div class="job-co">${esc([a.company, a.location].filter(Boolean).join(" · ") || "Company not given")}</div>
        </div>
        <div class="job-actions">
          ${a.url ? `<a class="icon-btn" href="${esc(a.url)}" target="_blank" rel="noopener" title="Open job link">🔗</a>` : ""}
          <button class="icon-btn" data-delete="${a.id}" title="Delete">🗑️</button>
        </div>
      </div>
      <div class="job-tags">
        ${a.has_documents ? '<span class="tag">📝 CV & letter ready</span>' : '<span class="tag">No CV yet</span>'}
        ${a.applied_on ? `<span class="tag">📅 Applied ${esc(a.applied_on)}</span>` : ""}
        ${a.notes ? `<span class="tag" title="${esc(a.notes)}">🗒️ Note</span>` : ""}
      </div>
      <div class="job-scores">${scoreBlock("Resume fit", a.fit_score)}${scoreBlock("Interview chance", a.interview_chance)}</div>
      <div class="job-foot"><span>${ago(a.created_at)}</span>${statusSelect(a)}</div>
    </article>`).join("");
  $("#apps-empty").classList.toggle("hidden", rows.length > 0);
  $("#apps-empty").textContent = apps.length ? "No applications match this filter." : "No jobs yet. Analyze your first one ✨";
}
$("#search").addEventListener("input", () => {
  if (!$("#view-dashboard").classList.contains("active")) show("dashboard"); else renderApps();
});
$("#filter-status").addEventListener("change", renderApps);

$("#apps-grid").addEventListener("change", async e => {
  const sel = e.target.closest(".status-select");
  if (!sel) return;
  try {
    await api(`/api/applications/${sel.dataset.id}`, { method: "PATCH", body: { status: sel.value } });
    toast(`Moved to ${sel.value}`);
    loadDashboard();
  } catch (err) { toast(err.message, true); }
});
$("#apps-grid").addEventListener("click", async e => {
  const del = e.target.closest("[data-delete]");
  const open = e.target.closest("[data-open]");
  if (open && !del && !e.target.closest("a")) return openApp(open.dataset.open);
  if (del) {
    const app = apps.find(a => a.id == del.dataset.delete);
    if (!confirm(`Delete "${app?.title || "this job"}" from your tracker? This can't be undone.`)) return;
    try { await api(`/api/applications/${del.dataset.delete}`, { method: "DELETE" }); toast("Deleted"); loadDashboard(); }
    catch (err) { toast(err.message, true); }
  }
});

// ---------- analysis rendering (shared by the Analyze tab and the modal) ----------
function gauge(value, label) {
  return `<div class="gauge" style="--v:${value};--col:${scoreColor(value)}"><div><b>${value}%</b><span>${label}</span></div></div>`;
}
function analysisHtml(app) {
  const a = app.analysis;
  if (!a) return "<p class='muted'>No analysis stored.</p>";
  const sub = (label, v) => `<div class="sub"><label>${label}<span>${v}%</span></label><div class="track"><i style="width:${v}%;background:${scoreColor(v)}"></i></div></div>`;
  const list = (cls, title, items) => `<div class="list-box ${cls}"><h3>${title}</h3><ul>${(items || []).map(i => `<li>${esc(i)}</li>`).join("")}</ul></div>`;
  return `
    <div class="result-head">
      ${gauge(a.fit_score, "resume fit")}
      ${gauge(a.interview_chance, "interview chance")}
      <div class="result-title">
        <h2>${esc(a.job_title || "Role")}</h2>
        <div class="company">${esc([a.company, a.location].filter(Boolean).join(" · "))} &nbsp;<span class="pill" style="background:${scoreColor(a.interview_chance)}">${esc(a.chance_label)} chance</span></div>
        <div class="verdict">${esc(a.verdict)}</div>
      </div>
    </div>
    <div class="subscores">
      ${sub("Skills", a.skill_match)}${sub("Experience", a.experience_match)}${sub("Education", a.education_match)}${sub("Keywords", a.keyword_match)}
    </div>
    <h3>✅ Skills you match</h3>
    <div class="chips">${a.matched_skills.map(s => `<span class="chip ok">${esc(s)}</span>`).join("") || "<span class='muted'>None found</span>"}</div>
    <h3>⚠️ Skills missing from your resume</h3>
    <div class="chips">${a.missing_skills.map(s => `<span class="chip miss">${esc(s)}</span>`).join("") || "<span class='muted'>Nothing major</span>"}</div>
    <h3>🔑 ATS keywords for this role</h3>
    <div class="chips">${a.ats_keywords.map(s => `<span class="chip kw">${esc(s)}</span>`).join("")}</div>
    <div class="lists" style="margin-top:22px">
      ${list("lb-green", "💪 Strengths", a.strengths)}
      ${list("lb-rose", "🧩 Gaps", a.gaps)}
      ${list("lb-violet", "🚀 How to improve", a.improvements)}
    </div>
    <p class="muted" style="margin-top:14px">The interview chance is an AI estimate from your resume and this posting only. It can't see the number of applicants, referrals or the employer's internal criteria.</p>`;
}

function documentsHtml(app) {
  if (!app.cv) {
    return `<div class="card glass" style="text-align:center">
      <h2>📝 Tailored CV & cover letter</h2>
      <p class="hint">Create a CV rewritten for this role and a matching cover letter. Only facts from your resume are used.</p>
      <button class="btn primary big" data-gen="${app.id}">✨ Create CV & cover letter</button></div>`;
  }
  return `<div class="card glass docs" data-app="${app.id}">
    <div class="card-head"><h2>📝 Tailored CV & cover letter</h2>
      <button class="btn small ghost" data-gen="${app.id}">🔄 Regenerate</button></div>
    <div class="doc-tabs">
      <button class="doc-tab active" data-doc="cv">📄 CV</button>
      <button class="doc-tab" data-doc="cover_letter">✉️ Cover letter</button>
    </div>
    <div class="doc-pane"></div>
    <div class="actions">
      <button class="btn small" data-act="edit">✏️ Edit</button>
      <button class="btn small" data-act="copy">📋 Copy</button>
      <button class="btn small" data-act="word">⬇️ Word</button>
      <button class="btn small" data-act="txt">⬇️ Text</button>
      <button class="btn small primary" data-act="pdf">🖨️ Save as PDF</button>
    </div>
    ${app.doc_notes?.length ? `<div class="notes-box"><b>💡 Before you send</b><ul>${app.doc_notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
  </div>`;
}

function wireDocuments(container, app, rerender) {
  const box = $(".docs", container);
  if (!box) return;
  let current = "cv", editing = false;
  const pane = $(".doc-pane", box);
  const draw = () => {
    const text = app[current];
    pane.innerHTML = editing
      ? `<textarea class="doc-edit">${esc(text)}</textarea><div class="actions"><button class="btn small" data-act="cancel">Cancel</button><button class="btn small primary" data-act="save">💾 Save changes</button></div>`
      : `<div class="doc">${current === "cv" ? md(text) : esc(text).split(/\n\s*\n/).map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("")}</div>`;
  };
  draw();
  box.addEventListener("click", async e => {
    const t = e.target.closest("[data-doc]");
    if (t) { current = t.dataset.doc; editing = false; $$("[data-doc]", box).forEach(b => b.classList.toggle("active", b === t)); return draw(); }
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    const name = `${current === "cv" ? "CV" : "Cover Letter"} - ${fileBase(app)}`;
    const text = app[current];
    const htmlBody = current === "cv" ? md(text) : esc(text).split(/\n\s*\n/).map(p => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
    if (act === "edit") { editing = true; draw(); }
    if (act === "cancel") { editing = false; draw(); }
    if (act === "save") {
      const value = $(".doc-edit", pane).value;
      try {
        const updated = await api(`/api/applications/${app.id}`, { method: "PATCH", body: { [current]: value } });
        app[current] = updated[current]; editing = false; draw(); toast("Saved");
      } catch (err) { toast(err.message, true); }
    }
    if (act === "copy") { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
    if (act === "txt") download(name + (current === "cv" ? ".md" : ".txt"), text, "text/plain;charset=utf-8");
    if (act === "word") download(name + ".doc", wordDoc(name, htmlBody), "application/msword");
    if (act === "pdf") {
      const doc = $(".doc", pane);
      if (!doc) return toast("Save or cancel your edit first.", true);
      doc.classList.add("print-area");
      const title = document.title; document.title = name;
      window.print();
      document.title = title; doc.classList.remove("print-area");
    }
  });
}

async function generateDocs(id, after) {
  await busy("Writing your tailored CV and cover letter… (about a minute)", async () => {
    const app = await api(`/api/applications/${id}/documents`, { method: "POST" });
    toast("CV and cover letter ready ✨");
    after(app);
  });
}

// ---------- analyze tab ----------
const jdText = $("#job-text");
jdText.addEventListener("input", () => { $("#jd-count").textContent = `${jdText.value.length.toLocaleString()} characters`; });

$("#btn-fetch").addEventListener("click", async () => {
  const url = $("#job-url").value.trim();
  if (!url) return toast("Paste a job link first.", true);
  await busy("Reading the job page…", async () => {
    const res = await api("/api/fetch-job", { method: "POST", body: { url } });
    jdText.value = res.text;
    jdText.dispatchEvent(new Event("input"));
    toast("Job description loaded. Check it, then press Check my fit.");
  });
});

function renderResult(app) {
  const box = $("#result");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="card glass">${analysisHtml(app)}
      <div class="actions"><button class="btn" data-goto="dashboard">📊 See it in my tracker</button></div></div>
    ${documentsHtml(app)}`;
  $$("[data-gen]", box).forEach(b => b.addEventListener("click", () => generateDocs(app.id, renderResult)));
  wireDocuments(box, app);
}

$("#btn-analyze").addEventListener("click", async () => {
  const jd = jdText.value.trim();
  if (jd.length < 150) return toast("Paste the full job description first (or fetch it from the link).", true);
  await busy("Comparing your resume with the job…", async () => {
    const app = await api("/api/analyze", { method: "POST", body: { job_description: jd, url: $("#job-url").value.trim() } });
    renderResult(app);
    toast("Analysis saved to your tracker");
    $("#result").scrollIntoView({ behavior: "smooth" });
  });
});

// ---------- application modal ----------
async function openApp(id) {
  try {
    const app = await api(`/api/applications/${id}`);
    renderModal(app);
    $("#modal").classList.remove("hidden");
  } catch (e) { toast(e.message, true); }
}
function renderModal(app) {
  const body = $("#modal-body");
  body.innerHTML = `
    <div class="track-row">
      <label>Status
        <select id="m-status">${STATUSES.map(s => `<option ${s === app.status ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <label>Applied on <input id="m-date" type="date" value="${esc(app.applied_on)}"></label>
      <label>Notes <input id="m-notes" type="text" value="${esc(app.notes)}" placeholder="Recruiter name, next step, salary…"></label>
    </div>
    <div class="card">${analysisHtml(app)}</div>
    ${documentsHtml(app)}
    <details class="jd"><summary>📋 Job description${app.url ? ` · <a href="${esc(app.url)}" target="_blank" rel="noopener">open link</a>` : ""}</summary><pre>${esc(app.job_description)}</pre></details>`;
  const save = async changes => {
    try { await api(`/api/applications/${app.id}`, { method: "PATCH", body: changes }); toast("Saved"); }
    catch (e) { toast(e.message, true); }
  };
  $("#m-status").addEventListener("change", e => save({ status: e.target.value }).then(() => openApp(app.id)));
  $("#m-date").addEventListener("change", e => save({ applied_on: e.target.value }));
  $("#m-notes").addEventListener("change", e => save({ notes: e.target.value }));
  $$("[data-gen]", body).forEach(b => b.addEventListener("click", () => generateDocs(app.id, renderModal)));
  wireDocuments(body, app);
}
function closeModal() { $("#modal").classList.add("hidden"); loadDashboard(); }
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal(); });

// ---------- resume tab ----------
async function loadResume() {
  try {
    const r = await api("/api/resume");
    $("#resume-text").value = r.text;
    resumeText = r.text;
    renderProfile();
    $("#resume-meta").textContent = r.updated_at ? `Last saved ${r.updated_at.replace("T", " ")}` : "No resume saved yet";
    if (!r.text) toast("Welcome! Start by adding your resume in the My Resume tab.");
  } catch (e) { toast(e.message, true); }
}
$("#resume-file").addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  await busy("Reading your file…", async () => {
    const data = await new Promise((ok, fail) => {
      const reader = new FileReader();
      reader.onload = () => ok(reader.result.split(",")[1]);
      reader.onerror = fail;
      reader.readAsDataURL(file);
    });
    const res = await api("/api/resume/file", { method: "POST", body: { name: file.name, data } });
    $("#resume-text").value = res.text;
    toast("File read. Check the text, then press Save resume.");
  });
  e.target.value = "";
});
$("#btn-save-resume").addEventListener("click", async () => {
  const text = $("#resume-text").value.trim();
  if (text.length < 100) return toast("That resume looks too short.", true);
  await busy("Saving…", async () => {
    await api("/api/resume", { method: "PUT", body: { text } });
    await loadResume();
    toast("Resume saved ✅");
  });
});

renderProfile();
loadResume().then(loadDashboard);
