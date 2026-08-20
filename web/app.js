const FONTE_LABELS = {
  ecoagro: "Ecoagro",
  opea: "Opea",
  riza: "Riza",
  vert: "Vert",
  bari: "Bari",
};

const PAGE_SIZE = 50;
const API_BASE =
  location.port === "8080" || location.hostname === "localhost"
    ? "http://127.0.0.1:8081"
    : "";

const els = {
  form: document.getElementById("filters"),
  company: document.getElementById("filter-company"),
  companies: document.getElementById("company-options"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
  fonte: document.getElementById("filter-fonte"),
  type: document.getElementById("filter-type"),
  reset: document.getElementById("reset-filters"),
  list: document.getElementById("document-list"),
  status: document.getElementById("status"),
  more: document.getElementById("load-more"),
  count: document.getElementById("result-count"),
  sheet: document.getElementById("sheet"),
  sheetBody: document.getElementById("sheet-body"),
  sheetTitle: document.getElementById("sheet-title"),
};

let offset = 0;
let total = 0;
let loading = false;

function queryFromForm() {
  const params = new URLSearchParams();
  if (els.company.value.trim()) params.set("devedor", els.company.value.trim());
  if (els.from.value) params.set("date_from", els.from.value);
  if (els.to.value) params.set("date_to", els.to.value);
  if (els.fonte.value) params.set("fonte", els.fonte.value);
  if (els.type.value) params.set("tipo_documento", els.type.value);
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

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
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

function fonteLabel(fonte) {
  return FONTE_LABELS[fonte] || fonte || "—";
}

function setStatus(message, show = true) {
  els.status.hidden = !show;
  els.status.textContent = message || "";
}

async function loadFilters() {
  const data = await api("/api/filters");
  for (const fonte of data.fontes || []) {
    els.fonte.append(option(fonte, fonteLabel(fonte)));
  }
  for (const tipo of data.tipos || []) {
    els.type.append(option(tipo, tipo));
  }
  for (const company of data.companies || []) {
    els.companies.append(option(company, company));
  }
}

function renderRows(items, append) {
  if (!append) els.list.replaceChildren();
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "row";
    button.innerHTML = `
      <span>${escapeHtml(dash(item.company))}</span>
      <span class="muted">${escapeHtml(formatDate(item.date))}</span>
      <span>${escapeHtml(dash(item.document_type))}</span>
    `;
    button.addEventListener("click", () => openDetail(item.id));
    els.list.append(button);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadDocuments(append = false) {
  if (loading) return;
  loading = true;
  if (!append) setStatus("Loading…");
  try {
    const data = await api(`/api/documents?${queryFromForm().toString()}`);
    total = data.total || 0;
    renderRows(data.items || [], append);
    offset = (data.offset || 0) + (data.items || []).length;
    els.more.hidden = !data.has_more;
    els.count.textContent = total === 1 ? "1 document" : `${total} documents`;
    if (total === 0) setStatus("No documents match these filters.");
    else setStatus("", false);
  } catch (error) {
    setStatus(error.message || "Could not load documents.");
  } finally {
    loading = false;
  }
}

function kv(label, value, isLink = false) {
  const display = dash(value);
  const inner = isLink && value
    ? `<a href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
    : escapeHtml(display);
  return `<div class="kv"><dt>${escapeHtml(label)}</dt><dd>${inner}</dd></div>`;
}

async function openDetail(id) {
  els.sheet.hidden = false;
  els.sheetTitle.textContent = "Document";
  els.sheetBody.innerHTML = `<p class="status">Loading…</p>`;
  try {
    const doc = await api(`/api/documents/${id}`);
    els.sheetTitle.textContent = doc.title || doc.document_type || "Document";
    const extras = doc.extras && Object.keys(doc.extras).length
      ? kv("Extras", JSON.stringify(doc.extras, null, 2))
      : "";
    els.sheetBody.innerHTML = [
      kv("Company", doc.company),
      kv("Date", formatDate(doc.date)),
      kv("Document type", doc.document_type),
      kv("Securitization company", fonteLabel(doc.fonte)),
      kv("Title", doc.title),
      kv("ISIN", doc.isin),
      kv("Emission number", doc.numero_emissao),
      kv("CETIP", doc.codigo_cetip),
      kv("Operation", doc.operacao),
      kv("Emission page", doc.emission_url, true),
      kv("Inserted", doc.inserted_at),
      extras,
      doc.url
        ? `<a class="open-link" href="${escapeHtml(doc.url)}" target="_blank" rel="noopener noreferrer">Open document</a>`
        : "",
    ].join("");
  } catch (error) {
    els.sheetBody.innerHTML = `<p class="status">${escapeHtml(error.message)}</p>`;
  }
}

function closeSheet() {
  els.sheet.hidden = true;
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  offset = 0;
  loadDocuments(false);
});

els.reset.addEventListener("click", () => {
  els.form.reset();
  offset = 0;
  loadDocuments(false);
});

els.more.addEventListener("click", () => loadDocuments(true));

els.sheet.addEventListener("click", (event) => {
  if (event.target.dataset.close) closeSheet();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeSheet();
});

loadFilters()
  .catch(() => setStatus("Could not load filters."))
  .finally(() => loadDocuments(false));
