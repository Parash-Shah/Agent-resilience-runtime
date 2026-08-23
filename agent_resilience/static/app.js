const state = { token: sessionStorage.getItem("agent-resilience-token") || "", role: null, incidents: [], selected: null, stream: null, action: null };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const formatTime = value => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const formatDuration = seconds => seconds >= 60 ? `${(seconds / 60).toFixed(1)}m` : `${Number(seconds || 0).toFixed(1)}s`;

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { connect(); throw new Error("Administrator token required"); }
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Request failed (${response.status})`); }
  return response.json();
}

function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 3500);
}

function connect() { if (!$("#token-dialog").open) $("#token-dialog").showModal(); }

async function loadHealth() {
  try {
    const health = await fetch("/health").then(r => r.json());
    $("#runtime-status").textContent = "Runtime healthy"; $("#runtime-mode").textContent = `${health.agent_mode} agent · durable queue`;
  } catch { $("#runtime-status").textContent = "Runtime unavailable"; }
}

async function loadDashboard() {
  if (!state.token) return connect();
  try {
    const status = $("#status-filter").value;
    const [session, summary, incidents] = await Promise.all([
      api("/v1/dashboard/session"),
      api("/v1/dashboard/summary"),
      api(`/v1/dashboard/incidents?limit=100${status ? `&status=${status}` : ""}`),
    ]);
    state.role = session.role;
    $("#runtime-mode").textContent = `${state.role} session · durable queue`;
    $("#new-incident").hidden = state.role !== "administrator";
    state.incidents = incidents;
    $("#success-rate").textContent = `${(summary.success_rate * 100).toFixed(0)}%`;
    $("#active-incidents").textContent = summary.active_incidents;
    $("#total-retries").textContent = summary.total_retries;
    $("#p95-latency").textContent = formatDuration(summary.p95_latency_seconds);
    $("#tool-failures").textContent = summary.tool_failures;
    $("#dlq-depth").textContent = summary.queue.DEAD || 0;
    $("#model-tokens").textContent = Number(summary.model_tokens || 0).toLocaleString();
    $("#loop-detections").textContent = summary.loop_detections;
    renderIncidents();
  } catch (error) { toast(error.message, true); }
}

function renderIncidents() {
  const body = $("#incident-list");
  if (!state.incidents.length) { body.innerHTML = '<tr><td colspan="6" class="empty">No incidents match this view.</td></tr>'; return; }
  body.innerHTML = state.incidents.map(item => {
    const progress = Math.min(100, Math.round(item.completed_steps.length / 6 * 100));
    return `<tr data-id="${escapeHtml(item.task_id)}"><td><strong class="mono">${escapeHtml(item.task_id)}</strong><br><span class="muted">${escapeHtml(item.goal.slice(0, 62))}</span></td><td><span class="status ${item.status}">${item.status}</span></td><td class="mono">${escapeHtml(item.current_step || "—")}</td><td>${progress}% · ${item.completed_steps.length}/6</td><td>${item.retries}</td><td>${formatTime(item.updated_at)}</td></tr>`;
  }).join("");
  body.querySelectorAll("tr[data-id]").forEach(row => row.addEventListener("click", () => openIncident(row.dataset.id)));
}

async function openIncident(taskId) {
  state.selected = taskId;
  $("#detail-id").textContent = taskId; $("#detail-panel").classList.add("open"); $("#scrim").classList.add("open");
  $("#detail-panel").setAttribute("aria-hidden", "false");
  try { renderDetail({ state: await api(`/v1/incidents/${taskId}`), events: await api(`/v1/incidents/${taskId}/events`) }); startStream(taskId); }
  catch (error) { toast(error.message, true); }
}

function renderDetail(snapshot) {
  const incident = snapshot.state, events = snapshot.events || [];
  const steps = ["read_alert", "inspect_metrics", "query_logs", "dependency_health", "restart_service", "verify_recovery"];
  const approval = incident.pending_action ? `<div class="approval-card"><strong>Approval required · ${incident.pending_action.risk}</strong><p>${escapeHtml(incident.pending_action.rationale)}</p><code>${escapeHtml(incident.pending_action.tool)}(${escapeHtml(JSON.stringify(incident.pending_action.arguments))})</code>${state.role === "administrator" ? '<div class="approval-actions"><button class="primary-button" data-action="approve">Approve</button><button class="danger-button" data-action="reject">Reject</button></div>' : '<p class="muted" style="margin-top:12px">Administrator role required to decide this action.</p>'}</div>` : "";
  const evidence = Object.entries(incident.evidence || {}).map(([name, value]) => `<details><summary>${escapeHtml(name)}</summary><pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`).join("") || '<p class="muted">Evidence has not been collected yet.</p>';
  const timeline = events.slice().reverse().map(event => `<div class="event"><strong>${escapeHtml(event.event_type)}</strong><time>${formatTime(event.created_at)}</time></div>`).join("");
  $("#detail-content").innerHTML = `<div class="summary-block"><span class="status ${incident.status}">${incident.status}</span><p style="margin-top:12px">${escapeHtml(incident.goal)}</p></div><div class="summary-block"><h3>Execution checkpoint</h3><div class="step-list">${steps.map(step => `<div class="step ${incident.completed_steps.includes(step) ? "done" : incident.current_step === step ? "current" : ""}">${incident.completed_steps.includes(step) ? "✓ " : ""}${step}</div>`).join("")}</div></div>${approval}<div class="summary-block"><h3>Diagnosis</h3><p>${escapeHtml(incident.diagnosis || incident.last_error || "Agent investigation in progress.")}</p></div><div class="summary-block"><h3>Evidence</h3>${evidence}</div><div class="summary-block"><h3>Audit timeline</h3><div class="timeline">${timeline}</div></div>`;
  $("#detail-content").querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => reviewAction(button.dataset.action, incident)));
}

async function startStream(taskId) {
  if (state.stream) state.stream.abort();
  const controller = new AbortController(); state.stream = controller;
  try {
    const response = await fetch(`/v1/dashboard/incidents/${taskId}/stream`, { headers: { Authorization: `Bearer ${state.token}` }, signal: controller.signal });
    if (!response.ok) throw new Error("Live stream unavailable");
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break; buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n"); buffer = frames.pop();
      frames.forEach(frame => { const line = frame.split("\n").find(item => item.startsWith("data: ")); if (line && state.selected === taskId) renderDetail(JSON.parse(line.slice(6))); });
    }
    loadDashboard();
  } catch (error) { if (error.name !== "AbortError") toast(error.message, true); }
}

function reviewAction(action, incident) {
  state.action = { action, incident };
  $("#action-eyebrow").textContent = action === "approve" ? "HUMAN APPROVAL" : "ACTION REJECTION";
  $("#action-title").textContent = action === "approve" ? "Approve bounded remediation" : "Reject proposed remediation";
  $("#action-summary").textContent = `${incident.pending_action.tool} · ${incident.pending_action.risk} risk · ${incident.pending_action.rationale}`;
  $("#action-reason").value = action === "approve" ? "Evidence supports this bounded remediation." : "Risk is not acceptable at this time.";
  $("#action-submit").textContent = action === "approve" ? "Approve action" : "Reject action";
  $("#action-dialog").showModal();
}

async function loadDlq() {
  if (!state.token) return connect();
  try {
    const records = await api("/v1/dashboard/dead-letters");
    $("#dlq-list").innerHTML = records.length ? records.map((record, index) => `<tr><td class="mono">${escapeHtml(record.task_id)}</td><td>${record.attempts}/${record.max_attempts}</td><td>${escapeHtml(record.last_error || "—")}</td><td>${formatTime(record.updated_at)}</td><td>${state.role === "administrator" ? `<button class="secondary-button" data-replay="${index}">Replay</button>` : '<span class="muted">Read only</span>'}</td></tr>`).join("") : '<tr><td colspan="5" class="empty">The dead-letter queue is empty.</td></tr>';
    $("#dlq-list").querySelectorAll("[data-replay]").forEach(button => button.addEventListener("click", () => replayDlq(records[Number(button.dataset.replay)])));
  } catch (error) { toast(error.message, true); }
}

async function replayDlq(record) {
  const reason = prompt(`Reason for replaying ${record.task_id}:`, "Transient dependency has recovered; resume from checkpoint.");
  if (!reason) return;
  try { await api("/v1/dashboard/dead-letters/replay", { method: "POST", body: JSON.stringify({ delivery_id: record.id, task_id: record.task_id, actor: "dashboard-operator", reason }) }); toast("Dead-letter workflow queued from its checkpoint."); loadDlq(); loadDashboard(); }
  catch (error) { toast(error.message, true); }
}

function closeDetail() { state.selected = null; if (state.stream) state.stream.abort(); $("#detail-panel").classList.remove("open"); $("#scrim").classList.remove("open"); $("#detail-panel").setAttribute("aria-hidden", "true"); }

$("#token-form").addEventListener("submit", event => { event.preventDefault(); state.token = $("#admin-token").value.trim(); sessionStorage.setItem("agent-resilience-token", state.token); $("#token-dialog").close(); loadDashboard(); });
$("#disconnect").addEventListener("click", () => { state.token = ""; sessionStorage.removeItem("agent-resilience-token"); connect(); });
$("#new-incident").addEventListener("click", () => $("#incident-dialog").showModal());
$("#cancel-incident").addEventListener("click", () => $("#incident-dialog").close());
$("#cancel-action").addEventListener("click", () => $("#action-dialog").close());
$("#incident-form").addEventListener("submit", async event => { event.preventDefault(); try { const incident = await api("/v1/incidents", { method: "POST", body: JSON.stringify({ goal: $("#incident-goal").value }) }); $("#incident-dialog").close(); toast("Incident accepted into the durable queue."); await loadDashboard(); openIncident(incident.task_id); } catch (error) { toast(error.message, true); } });
$("#action-form").addEventListener("submit", async event => { event.preventDefault(); const { action, incident } = state.action; try { await api(`/v1/incidents/${incident.task_id}/${action}`, { method: "POST", body: JSON.stringify({ actor: $("#action-actor").value, reason: $("#action-reason").value }) }); $("#action-dialog").close(); toast(action === "approve" ? "Action approved and workflow resumed." : "Action rejected and workflow stopped."); openIncident(incident.task_id); loadDashboard(); } catch (error) { toast(error.message, true); } });
$("#close-detail").addEventListener("click", closeDetail); $("#scrim").addEventListener("click", closeDetail);
$("#refresh").addEventListener("click", loadDashboard); $("#status-filter").addEventListener("change", loadDashboard); $("#refresh-dlq").addEventListener("click", loadDlq);
document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => { document.querySelectorAll(".nav-item,.view").forEach(node => node.classList.remove("active")); button.classList.add("active"); $(`#${button.dataset.view}-view`).classList.add("active"); if (button.dataset.view === "dead-letters") loadDlq(); }));

loadHealth(); if (state.token) loadDashboard(); else connect(); setInterval(() => { if (state.token) loadDashboard(); }, 5000);
