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
  cetip: document.getElementById("filter-cetip"),
  isin: document.getElementById("filter-isin"),
  fonte: document.getElementById("filter-fonte"),
  reset: document.getElementById("reset-filters"),
  list: document.getElementById("serie-list"),
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
  if (els.company.value.trim()) params.set("company", els.company.value.trim());
  if (els.cetip.value.trim()) params.set("cetip", els.cetip.value.trim());
  if (els.isin.value.trim()) params.set("isin", els.isin.value.trim());
  if (els.fonte.value) params.set("fonte", els.fonte.value);
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
  const [year, month, day] = String(value).split("-");
  if (!day) return value;
  return `${day}/${month}/${year}`;
}

function fonteLabel(fonte) {
  return FONTE_LABELS[fonte] || fonte || "—";
}

function resolveUrl(url) {
  if (!url) return null;
  if (url.startsWith("/")) return `${API_BASE}${url}`;
  return url;
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

async function loadFilters() {
  const data = await api("/api/series/filters");
  for (const fonte of data.fontes || []) {
    els.fonte.append(option(fonte, fonteLabel(fonte)));
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
    button.className = "row row-emissoes";
    button.innerHTML = `
      <span>${escapeHtml(dash(item.company))}</span>
      <span class="muted">${escapeHtml(fonteLabel(item.fonte))}</span>
      <span class="muted codes">${escapeHtml(dash(item.isin))}</span>
      <span class="muted codes">${escapeHtml(dash(item.codigo_cetip))}</span>
      <span class="muted">${escapeHtml(formatDate(item.data_emissao))}</span>
      <span class="muted">${escapeHtml(formatDate(item.data_vencimento))}</span>
    `;
    button.addEventListener("click", () => openDetail(item.id));
    els.list.append(button);
  }
}

async function loadSeries(append = false) {
  if (loading) return;
  loading = true;
  if (!append) setStatus("Loading…");
  try {
    const data = await api(`/api/series?${queryFromForm().toString()}`);
    total = data.total || 0;
    renderRows(data.items || [], append);
    offset = (data.offset || 0) + (data.items || []).length;
    els.more.hidden = !data.has_more;
    els.count.textContent = total === 1 ? "1 série" : `${total} séries`;
    if (total === 0) setStatus("No séries match these filters.");
    else setStatus("", false);
  } catch (error) {
    setStatus(error.message || "Could not load séries.");
  } finally {
    loading = false;
  }
}

function kv(label, value, isLink = false) {
  const display = dash(value);
  const inner =
    isLink && value
      ? `<a href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
      : escapeHtml(display);
  return `<div class="kv"><dt>${escapeHtml(label)}</dt><dd>${inner}</dd></div>`;
}

function renderDocuments(documentos) {
  if (!documentos || !documentos.length) {
    return `<p class="section-empty">No documents for this série.</p>`;
  }
  const items = documentos
    .map((doc) => {
      const title = doc.titulo || doc.tipo_documento || "Document";
      const meta = [doc.tipo_documento, formatDate(doc.data_documento)]
        .filter((part) => part && part !== "—")
        .join(" · ");
      const href = resolveUrl(doc.url);
      const link = href
        ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`
        : escapeHtml(title);
      return `<li class="doc-item"><div class="doc-title">${link}</div><div class="muted">${escapeHtml(meta || "—")}</div></li>`;
    })
    .join("");
  return `<ul class="doc-list">${items}</ul>`;
}

async function openDetail(id) {
  els.sheet.hidden = false;
  els.sheetTitle.textContent = "Série";
  els.sheetBody.innerHTML = `<p class="status">Loading…</p>`;
  try {
    const data = await api(`/api/series/${id}`);
    const extras = data.extras || {};
    els.sheetTitle.textContent =
      data.company || data.operacao || `Série ${dash(data.numero_serie)}`;
    els.sheetBody.innerHTML = [
      `<section class="detail-section"><h3>Série</h3>`,
      kv("Company", data.company),
      kv("Operation", data.operacao),
      kv("Debtor", data.devedor),
      kv("Securitization company", fonteLabel(data.fonte)),
      kv("Emission number", data.numero_emissao),
      kv("Série number", data.numero_serie),
      kv("ISIN", data.isin),
      kv("CETIP", data.codigo_cetip),
      kv("Issue date", formatDate(data.data_emissao)),
      kv("Maturity", formatDate(data.data_vencimento)),
      kv("Interest", data.remuneracao),
      kv("Indexer", data.indexador),
      kv("Qty issued", data.quantidade),
      kv("Volume", data.valor),
      kv("Class", extras.classe),
      kv("Concentration", extras.concentracao),
      kv("Fiduciary agent", extras.agente_fiduciario),
      kv("Segment", extras.segmento),
      kv("Portal page", data.fonte === "opea" ? null : data.link, true),
      `</section>`,
      `<section class="detail-section"><h3>Documents (${(data.documentos || []).length})</h3>`,
      renderDocuments(data.documentos),
      `</section>`,
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
  loadSeries(false);
});

els.reset.addEventListener("click", () => {
  els.form.reset();
  offset = 0;
  loadSeries(false);
});

els.more.addEventListener("click", () => loadSeries(true));

els.sheet.addEventListener("click", (event) => {
  if (event.target.dataset.close) closeSheet();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeSheet();
});

loadFilters()
  .catch(() => setStatus("Could not load filters."))
  .finally(() => loadSeries(false));
