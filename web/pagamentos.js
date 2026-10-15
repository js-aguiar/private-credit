const PAGE_SIZE = 50;
const API_BASE =
  location.port === "8080" || location.hostname === "localhost"
    ? "http://127.0.0.1:8081"
    : "";

const els = {
  form: document.getElementById("filters"),
  cetip: document.getElementById("filter-cetip"),
  reset: document.getElementById("reset-filters"),
  body: document.getElementById("agenda-body"),
  status: document.getElementById("status"),
  more: document.getElementById("load-more"),
  count: document.getElementById("result-count"),
};

let offset = 0;
let total = 0;
let loading = false;

function queryFromForm() {
  const params = new URLSearchParams();
  if (els.cetip.value.trim()) params.set("cetip", els.cetip.value.trim());
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

async function api(path) {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

function dash(value) {
  return value == null || value === "" ? "—" : value;
}

function formatDate(value) {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  if (!day) return value;
  return `${day}/${month}/${year}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message, show = true) {
  els.status.hidden = !show;
  els.status.textContent = message || "";
}

function renderRows(items, append) {
  if (!els.body) return;
  if (!append) els.body.replaceChildren();
  for (const item of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${escapeHtml(dash(item.codigo_cetip))}</td>
      <td>${escapeHtml(formatDate(item.data_evento))}</td>
      <td>${escapeHtml(formatDate(item.data_liquidacao))}</td>
      <td>${escapeHtml(dash(item.tipo_evento))}</td>
      <td class="num">${escapeHtml(dash(item.taxa))}</td>
      <td class="num">${escapeHtml(dash(item.valor_pago))}</td>
      <td>${escapeHtml(dash(item.status))}</td>
    `;
    els.body.append(tr);
  }
}

async function loadAgenda(append = false) {
  if (loading) return;
  loading = true;
  if (!append) setStatus("Loading…");
  try {
    const data = await api(`/api/agenda?${queryFromForm().toString()}`);
    total = data.total || 0;
    renderRows(data.items || [], append);
    offset = (data.offset || 0) + (data.items || []).length;
    els.more.hidden = !data.has_more;
    els.count.textContent =
      total === 1 ? "1 payment event" : `${total} payment events`;
    if (total === 0) setStatus("No payment events match this filter.");
    else setStatus("", false);
  } catch (error) {
    setStatus(error.message || "Could not load payment events.");
  } finally {
    loading = false;
  }
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  offset = 0;
  loadAgenda(false);
});

els.reset.addEventListener("click", () => {
  els.form.reset();
  offset = 0;
  loadAgenda(false);
});

els.more.addEventListener("click", () => loadAgenda(true));

if (!els.body) {
  setStatus("Page shell is out of date. Hard-refresh (Ctrl/Cmd+Shift+R) and try again.");
} else {
  loadAgenda(false);
}
