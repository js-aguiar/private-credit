const FONTE_LABELS = {
  ecoagro: "Ecoagro",
  opea: "Opea",
  riza: "Riza",
  vert: "Vert",
  bari: "Bari",
};

const API_BASE =
  location.port === "8080" || location.hostname === "localhost"
    ? "http://127.0.0.1:8081"
    : "";

const els = {
  select: document.getElementById("portfolio-select"),
  name: document.getElementById("portfolio-name"),
  create: document.getElementById("create-portfolio"),
  removePortfolio: document.getElementById("delete-portfolio"),
  draftHint: document.getElementById("draft-hint"),
  query: document.getElementById("series-query"),
  search: document.getElementById("search-series"),
  clearSearch: document.getElementById("clear-search"),
  searchResults: document.getElementById("search-results"),
  list: document.getElementById("portfolio-series"),
  status: document.getElementById("status"),
  count: document.getElementById("result-count"),
};

/** @type {{ mode: "idle"|"draft"|"saved", nome: string|null, series: any[] }} */
let state = { mode: "idle", nome: null, series: [] };

function dash(value) {
  return value == null || value === "" ? "—" : value;
}

function fonteLabel(fonte) {
  return FONTE_LABELS[fonte] || fonte || "—";
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

function portfolioPath(nome) {
  return `/api/portfolios/${encodeURIComponent(nome)}`;
}

function localAuthHeaders() {
  // Local API enforces the same Basic Auth as production CloudFront.
  if (!API_BASE) return {};
  return { Authorization: "Basic " + btoa("admin:cloudf@990055") };
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...localAuthHeaders(),
      ...(options.headers || {}),
    },
  });
  let data = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: "invalid_json", message: text };
    }
  }
  if (!response.ok) {
    const message =
      (data && (data.message || data.error)) || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function updateChrome() {
  const n = state.series.length;
  if (state.mode === "idle") {
    els.count.textContent = "No portfolio selected";
  } else if (state.mode === "draft") {
    els.count.textContent = n === 1 ? "Draft · 1 série" : `Draft · ${n} séries`;
  } else {
    els.count.textContent = n === 1 ? "1 série" : `${n} séries`;
  }
  els.draftHint.hidden = state.mode !== "draft";
  els.removePortfolio.hidden = state.mode !== "saved";
  els.name.value = state.mode === "idle" ? els.name.value : state.nome || "";
}

function renderSeries() {
  els.list.replaceChildren();
  if (!state.nome && state.mode === "idle") {
    setStatus("Select a portfolio or create a new one.");
    updateChrome();
    return;
  }
  if (state.series.length === 0) {
    setStatus(
      state.mode === "draft"
        ? "Search CETIP or ISIN and add a série to save this portfolio."
        : "This portfolio has no séries."
    );
    updateChrome();
    return;
  }
  setStatus("", false);
  for (const item of state.series) {
    const row = document.createElement("div");
    row.className = "row row-portfolio";
    row.innerHTML = `
      <span>${escapeHtml(dash(item.company))}</span>
      <span class="muted">${escapeHtml(fonteLabel(item.fonte))}</span>
      <span class="muted codes">${escapeHtml(dash(item.isin))}</span>
      <span class="muted codes">${escapeHtml(dash(item.codigo_cetip))}</span>
      <span class="row-actions">
        <button type="button" class="ghost danger" data-remove="${item.id}">Remove</button>
      </span>
    `;
    row.querySelector("[data-remove]").addEventListener("click", () => removeSerie(item.id));
    els.list.append(row);
  }
  updateChrome();
}

async function refreshPortfolioList(selectNome) {
  const data = await api("/api/portfolios");
  const current = selectNome ?? els.select.value;
  els.select.replaceChildren(option("", "Select a portfolio…"));
  for (const item of data.items || []) {
    els.select.append(
      option(item.nome, `${item.nome} (${item.series_count})`)
    );
  }
  if (current) els.select.value = current;
  return data.items || [];
}

async function loadPortfolio(nome) {
  if (!nome) {
    state = { mode: "idle", nome: null, series: [] };
    renderSeries();
    return;
  }
  const data = await api(portfolioPath(nome));
  state = {
    mode: "saved",
    nome: data.nome,
    series: data.series || [],
  };
  els.select.value = data.nome;
  renderSeries();
}

async function createDraft() {
  const nome = els.name.value.trim();
  if (!nome) {
    setStatus("Enter a unique portfolio name.");
    return;
  }
  const items = await refreshPortfolioList();
  if (items.some((item) => item.nome.toLowerCase() === nome.toLowerCase())) {
    setStatus("A portfolio with this name already exists.");
    return;
  }
  state = { mode: "draft", nome, series: [] };
  els.select.value = "";
  renderSeries();
}

async function addSerie(serie) {
  if (!state.nome) {
    setStatus("Create or select a portfolio first.");
    return;
  }
  try {
    if (state.mode === "draft") {
      const created = await api("/api/portfolios", {
        method: "POST",
        body: JSON.stringify({ nome: state.nome, serie_id: serie.id }),
      });
      state = {
        mode: "saved",
        nome: created.nome,
        series: created.series || [],
      };
      await refreshPortfolioList(created.nome);
      els.select.value = created.nome;
    } else {
      const updated = await api(`${portfolioPath(state.nome)}/series`, {
        method: "POST",
        body: JSON.stringify({ serie_id: serie.id }),
      });
      state = {
        mode: "saved",
        nome: updated.nome,
        series: updated.series || [],
      };
      await refreshPortfolioList(updated.nome);
    }
    renderSeries();
    els.searchResults.hidden = true;
    els.searchResults.replaceChildren();
  } catch (error) {
    if (error.status === 409) setStatus("A portfolio with this name already exists.");
    else if (error.data && error.data.error === "serie_not_found") {
      setStatus("Série not found in the catalog.");
    } else setStatus(error.message || "Could not add série.");
  }
}

async function removeSerie(serieId) {
  if (!state.nome || state.mode !== "saved") return;
  try {
    const updated = await api(`${portfolioPath(state.nome)}/series/${serieId}`, {
      method: "DELETE",
    });
    if (updated.deleted) {
      state = { mode: "idle", nome: null, series: [] };
      await refreshPortfolioList("");
      els.select.value = "";
      els.name.value = "";
      setStatus("Portfolio removed after its last série was deleted.");
      updateChrome();
      els.list.replaceChildren();
      return;
    }
    state = {
      mode: "saved",
      nome: updated.nome,
      series: updated.series || [],
    };
    await refreshPortfolioList(updated.nome);
    renderSeries();
  } catch (error) {
    setStatus(error.message || "Could not remove série.");
  }
}

async function deleteCurrentPortfolio() {
  if (!state.nome || state.mode !== "saved") return;
  if (!window.confirm(`Delete portfolio “${state.nome}”?`)) return;
  try {
    await api(portfolioPath(state.nome), { method: "DELETE" });
    state = { mode: "idle", nome: null, series: [] };
    els.name.value = "";
    await refreshPortfolioList("");
    els.select.value = "";
    els.list.replaceChildren();
    setStatus("Portfolio deleted.");
    updateChrome();
  } catch (error) {
    setStatus(error.message || "Could not delete portfolio.");
  }
}

function renderSearchResults(items) {
  els.searchResults.replaceChildren();
  if (!items.length) {
    els.searchResults.hidden = false;
    els.searchResults.innerHTML = `<p class="status">No matching série in the catalog.</p>`;
    return;
  }
  els.searchResults.hidden = false;
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "search-hit";
    card.innerHTML = `
      <div>
        <strong>${escapeHtml(dash(item.company))}</strong>
        <p class="muted">${escapeHtml(fonteLabel(item.fonte))} · ${escapeHtml(dash(item.isin))} · ${escapeHtml(dash(item.codigo_cetip))}</p>
      </div>
      <button type="button">Add</button>
    `;
    card.querySelector("button").addEventListener("click", () => addSerie(item));
    els.searchResults.append(card);
  }
}

async function searchSeries() {
  const q = els.query.value.trim();
  if (!q) {
    setStatus("Enter a CETIP code or ISIN.");
    return;
  }
  try {
    const data = await api(`/api/series/lookup?q=${encodeURIComponent(q)}`);
    renderSearchResults(data.items || []);
    if ((data.items || []).length) setStatus("", false);
  } catch (error) {
    setStatus(error.message || "Search failed.");
  }
}

els.select.addEventListener("change", () => {
  loadPortfolio(els.select.value).catch((error) => {
    setStatus(error.message || "Could not load portfolio.");
  });
});
els.create.addEventListener("click", () => {
  createDraft().catch((error) => setStatus(error.message || "Could not create portfolio."));
});
els.removePortfolio.addEventListener("click", () => {
  deleteCurrentPortfolio().catch((error) => setStatus(error.message || "Delete failed."));
});
els.search.addEventListener("click", () => {
  searchSeries().catch((error) => setStatus(error.message || "Search failed."));
});
els.clearSearch.addEventListener("click", () => {
  els.query.value = "";
  els.searchResults.hidden = true;
  els.searchResults.replaceChildren();
});
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchSeries();
  }
});
els.name.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    createDraft();
  }
});

async function boot() {
  try {
    await refreshPortfolioList();
    state = { mode: "idle", nome: null, series: [] };
    renderSeries();
    els.count.textContent = "No portfolio selected";
  } catch (error) {
    setStatus(error.message || "Could not load portfolios.");
    els.count.textContent = "Error";
  }
}

boot();
