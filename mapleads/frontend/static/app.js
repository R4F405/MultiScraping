// ── Proxy status widget ──────────────────────────────────────
let _proxyStatus = null; // cached for capacity checks

async function loadProxyStatus() {
  try {
    const res = await fetch("/api/proxy/status");
    if (!res.ok) return;
    const data = await res.json();
    _proxyStatus = data;

    const widget = document.getElementById("proxy-widget");
    const dot = document.getElementById("proxy-dot");
    const label = document.getElementById("proxy-label");
    const reqLabel = document.getElementById("proxy-requests-label");
    const barFill = document.getElementById("proxy-bar-fill");
    const pctLabel = document.getElementById("proxy-pct-label");
    if (!widget) return;
    if (data.total_proxies === 0) return;
    widget.style.display = "flex";

    const available = data.available_now;
    const total = data.total_proxies;
    const dailyPct = data.daily_requests_limit > 0
      ? Math.round((data.daily_requests_used / data.daily_requests_limit) * 100) : 0;

    let color = "green";
    if (available <= 2 || dailyPct >= 80) color = "red";
    else if (available <= 5 || dailyPct >= 50) color = "yellow";

    dot.className = `proxy-dot ${color}`;
    barFill.className = `proxy-bar-fill ${color}`;
    label.textContent = `Proxies: ${available}/${total} disponibles`;
    reqLabel.textContent = `Requests hoy: ${data.daily_requests_used.toLocaleString()} / ${data.daily_requests_limit.toLocaleString()}`;
    barFill.style.width = dailyPct + "%";
    pctLabel.textContent = dailyPct + "%";

    // Block start button only when daily limit is exhausted (can't scrape at all today)
    // When proxies are just in cooldown the scraping will auto-pause and resume
    const dailyExhausted = data.daily_requests_remaining === 0 && data.total_proxies > 0;
    if (!_scrapingInProgress) {
      const btn = document.getElementById("start-btn");
      if (btn) {
        btn.disabled = dailyExhausted;
        btn.title = dailyExhausted ? "Límite diario de requests agotado. Se reiniciará mañana." : "";
      }
    }
  } catch (_) {}
}
loadProxyStatus();
loadCapacity();
setInterval(loadProxyStatus, 30_000);
setInterval(loadCapacity, 60_000);

// ── Proxy capacity notice ─────────────────────────────────────
let _capacityData = null;

async function loadCapacity() {
  try {
    const res = await fetch("/api/proxy/capacity");
    if (!res.ok) return;
    _capacityData = await res.json();
    updateCapacityNotice();
  } catch (_) {}
}

function updateCapacityNotice() {
  const notice = document.getElementById("capacity-notice");
  if (!notice || !_capacityData || _capacityData.dev_mode) {
    if (notice) notice.style.display = "none";
    return;
  }

  const cap = _capacityData;
  const requested = getCounterValue();

  if (cap.daily_remaining === 0) {
    showCapacityNotice("⛔ Límite diario agotado. Podrás volver a scrapear mañana.", "danger");
    return;
  }

  if (cap.all_in_cooldown) {
    const secs = cap.next_available_seconds;
    const mins = Math.ceil(secs / 60);
    showCapacityNotice(
      `⏸ Todos los proxies en cooldown (~${mins > 1 ? mins + "min" : secs + "s"}). El scraping esperará automáticamente y continuará cuando estén disponibles.`,
      "warning"
    );
    return;
  }

  if (requested > cap.companies_before_wait) {
    const extra = requested - cap.companies_before_wait;
    // Estimate extra pause cycles: each cycle = cooldown_seconds
    const cooldownMin = Math.ceil(cap.cooldown_seconds / 60);
    const cycles = Math.ceil((extra * cap.requests_per_company_estimate) / (cap.requests_available_now || 1));
    const extraMin = cycles * cooldownMin;
    showCapacityNotice(
      `⚠ Con ${requested} empresas el scraping hará ~${cycles} pausa${cycles > 1 ? "s" : ""} de ~${cooldownMin}min (total +${extraMin}min). Se completará automáticamente.`,
      "warning"
    );
  } else {
    const remaining = cap.companies_before_wait - requested;
    showCapacityNotice(
      `✓ ${requested} empresas caben sin pausas (margen: ${remaining} más antes de refresco de proxies).`,
      "ok"
    );
  }
}

function showCapacityNotice(msg, type) {
  const notice = document.getElementById("capacity-notice");
  if (!notice) return;
  notice.textContent = msg;
  notice.className = `capacity-notice capacity-notice--${type}`;
  notice.style.display = "block";
}

// ── State ────────────────────────────────────────────────────
let currentJobId = null;
let pollInterval = null;
let _scrapingInProgress = false;

// ── Counter (numeric input, only numbers) ────────────────────
const counterInput = document.getElementById("counter-display");
const counterMinus = document.getElementById("counter-minus");
const counterPlus  = document.getElementById("counter-plus");

function getCounterValue() {
  return Math.max(1, parseInt(counterInput.value) || 1);
}

counterInput.addEventListener("input", function () {
  this.value = this.value.replace(/[^0-9]/g, "");
  updateCapacityNotice();
});

counterInput.addEventListener("blur", function () {
  if (!this.value || parseInt(this.value) < 1) this.value = "1";
  updateCapacityNotice();
});

counterInput.addEventListener("paste", function (e) {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text");
  const numeric = text.replace(/[^0-9]/g, "");
  if (numeric) this.value = numeric;
  updateCapacityNotice();
});

counterMinus.addEventListener("click", () => {
  counterInput.value = Math.max(1, getCounterValue() - 1);
  updateCapacityNotice();
});

counterPlus.addEventListener("click", () => {
  counterInput.value = getCounterValue() + 1;
  updateCapacityNotice();
});

document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", function () {
    counterInput.value = this.dataset.value;
    updateCapacityNotice();
  });
});

// ── Leaflet Map ──────────────────────────────────────────────
const map = L.map("map", {
  center: [20, 0],
  zoom: 2,
  minZoom: 2,
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

let selectedLat = null;
let selectedLng = null;
let circleLayer  = null;
let centerMarker = null;

function getRadiusMeters() {
  return parseInt(document.getElementById("radius-slider").value) * 1000;
}

function fitMapToCircle() {
  if (!circleLayer) return;
  map.fitBounds(circleLayer.getBounds().pad(0.2), { animate: true, duration: 0.5 });
}

// Custom Google Maps-style pin icon (draggable handle)
const pinIcon = L.divIcon({
  className: "map-pin-icon",
  html: `<svg width="28" height="36" viewBox="0 0 28 36" fill="none" xmlns="http://www.w3.org/2000/svg">
    <filter id="shadow" x="-20%" y="-10%" width="140%" height="130%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.25"/>
    </filter>
    <path d="M14 0C6.268 0 0 6.268 0 14C0 24.5 14 36 14 36C14 36 28 24.5 28 14C28 6.268 21.732 0 14 0Z"
          fill="#4285F4" filter="url(#shadow)"/>
    <circle cx="14" cy="14" r="5.5" fill="white"/>
  </svg>`,
  iconSize:   [28, 36],
  iconAnchor: [14, 36],
  popupAnchor: [0, -36],
});

function setMapLocation(lat, lng) {
  selectedLat = lat;
  selectedLng = lng;

  // Remove existing layers
  if (circleLayer)  map.removeLayer(circleLayer);
  if (centerMarker) map.removeLayer(centerMarker);

  const radius = getRadiusMeters();

  // Draw circle
  circleLayer = L.circle([lat, lng], {
    color:       "#4285F4",
    fillColor:   "#4285F4",
    fillOpacity: 0.1,
    radius,
    weight: 2,
  }).addTo(map);

  // Place draggable marker at center
  centerMarker = L.marker([lat, lng], {
    draggable: true,
    icon: pinIcon,
    zIndexOffset: 1000,
  }).addTo(map);

  // While dragging: move circle in real time
  centerMarker.on("drag", function (e) {
    const pos = e.target.getLatLng();
    circleLayer.setLatLng(pos);
    selectedLat = pos.lat;
    selectedLng = pos.lng;
  });

  // After drag ends: reverse geocode to update location input
  centerMarker.on("dragend", function (e) {
    const pos = e.target.getLatLng();
    reverseGeocode(pos.lat, pos.lng);
  });

  fitMapToCircle();
}

// Geocode a place name and fly to it
async function geocodeLocation(query) {
  if (!query.trim()) return false;
  try {
    const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1`;
    const res  = await fetch(url, { headers: { "Accept-Language": "es" } });
    const data = await res.json();
    if (data && data[0]) {
      setMapLocation(parseFloat(data[0].lat), parseFloat(data[0].lon));
      return true;
    }
  } catch (_) {}
  return false;
}

// Reverse geocode coords → update location input text
async function reverseGeocode(lat, lng) {
  try {
    const url  = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`;
    const res  = await fetch(url, { headers: { "Accept-Language": "es" } });
    const data = await res.json();
    if (data && data.address) {
      const a = data.address;
      const name = a.city || a.town || a.village || a.municipality || a.county
                || data.display_name.split(",")[0];
      document.getElementById("location").value = name;
    }
  } catch (_) {}
}

// Click anywhere on map → select that location
map.on("click", function (e) {
  setMapLocation(e.latlng.lat, e.latlng.lng);
  reverseGeocode(e.latlng.lat, e.latlng.lng);
});

// Radius slider: update circle + fit map
document.getElementById("radius-slider").addEventListener("input", function () {
  const radius = parseInt(this.value);
  document.getElementById("radius-label").textContent = radius + " km";
  if (circleLayer) {
    circleLayer.setRadius(radius * 1000);
    fitMapToCircle();
  }
});

// "Ir" button: geocode the typed location
document.getElementById("go-btn").addEventListener("click", async () => {
  const loc = document.getElementById("location").value.trim();
  if (loc) await geocodeLocation(loc);
});

// Enter in location input: same as clicking "Ir"
document.getElementById("location").addEventListener("keydown", async (e) => {
  if (e.key === "Enter") {
    const loc = document.getElementById("location").value.trim();
    if (loc) await geocodeLocation(loc);
  }
});

// ── Alert ────────────────────────────────────────────────────
const alertEl = document.getElementById("alert");

function showAlert(msg, type = "error") {
  alertEl.textContent = msg;
  alertEl.className   = `alert visible alert-${type}`;
}

function hideAlert() { alertEl.className = "alert"; }

// ── Loader ───────────────────────────────────────────────────
const loaderSection = document.getElementById("loader-section");
const loaderCount   = document.getElementById("loader-count");

function showLoader() {
  loaderSection.style.display = "flex";
}

function hideLoader() {
  loaderSection.style.display = "none";
}

function updateLoaderCount(progress, total, emailsFound, waitingForProxy, proxyWaitSeconds) {
  if (!loaderCount) return;
  const proxyWaitEl = document.getElementById("loader-proxy-wait");
  const spinnerEl = document.getElementById("loader-spinner");
  const textEl = document.getElementById("loader-text");

  if (total > 0) {
    loaderCount.textContent = `${progress} / ${total} negocios · ${emailsFound} emails encontrados`;
  } else {
    loaderCount.textContent = "Conectando con Google Maps...";
  }

  if (waitingForProxy) {
    const secs = proxyWaitSeconds > 0 ? proxyWaitSeconds : "?";
    const mins = proxyWaitSeconds > 60 ? ` (~${Math.ceil(proxyWaitSeconds / 60)}min)` : "";
    if (proxyWaitEl) {
      proxyWaitEl.textContent = `⏸ Proxies en cooldown — reanudando en ~${secs}s${mins}`;
      proxyWaitEl.style.display = "inline";
    }
    if (spinnerEl) spinnerEl.style.animationPlayState = "paused";
    if (textEl) textEl.textContent = "En pausa...";
  } else {
    if (proxyWaitEl) proxyWaitEl.style.display = "none";
    if (spinnerEl) spinnerEl.style.animationPlayState = "running";
    if (textEl) textEl.textContent = "Cargando emails...";
  }
}

// ── Results helpers ───────────────────────────────────────────
const tableBody    = document.getElementById("results-tbody");
const emptyState   = document.getElementById("empty-state");
const resultsCount = document.getElementById("results-count");
const exportBtn    = document.getElementById("export-btn");
const startBtn     = document.getElementById("start-btn");

const PAGE_SIZE = 25;
let _allLeads   = [];
let _currentPage = 1;

function escHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function emailStatusClass(status) {
  if (status === "valid")   return "email-valid";
  if (status === "invalid") return "email-invalid";
  return "email-pending";
}

function clearTable() {
  _allLeads    = [];
  _currentPage = 1;
  if (tableBody)     tableBody.innerHTML = "";
  if (emptyState)    emptyState.classList.add("hidden");
  if (resultsCount)  resultsCount.textContent = "";
  _updatePagination();
}

function _buildRow(lead) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td title="${escHtml(lead.business_name)}">${escHtml(lead.business_name) || "—"}</td>
    <td title="${escHtml(lead.address)}">${escHtml(lead.address) || "—"}</td>
    <td>${escHtml(lead.phone) || "—"}</td>
    <td class="link-cell">${lead.website
      ? `<a href="${escHtml(lead.website)}" target="_blank" rel="noopener">${escHtml(lead.website)}</a>`
      : "—"}</td>
    <td class="${emailStatusClass(lead.email_status)}">${escHtml(lead.email) || "—"}</td>
    <td>${escHtml(lead.email_status) || "—"}</td>
    <td>${lead.rating ?? "—"}</td>
    <td>
      <button class="btn btn-danger" onclick="deleteLead(${lead.id}, this.closest('tr'))">✕</button>
    </td>
  `;
  return tr;
}

function _renderPage(page) {
  if (!tableBody) return;
  const total = _allLeads.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  _currentPage = Math.min(Math.max(1, page), totalPages);

  const start = (_currentPage - 1) * PAGE_SIZE;
  const end   = Math.min(start + PAGE_SIZE, total);
  const slice = _allLeads.slice(start, end);

  tableBody.innerHTML = "";
  for (const lead of slice) {
    tableBody.appendChild(_buildRow(lead));
  }
  _updatePagination();
}

function _updatePagination() {
  const pagination = document.getElementById("pagination");
  const pageInfo   = document.getElementById("page-info");
  const total      = _allLeads.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  if (!pagination) return;

  // Hide pagination controls when everything fits on one page
  pagination.style.display = (total > PAGE_SIZE) ? "flex" : "none";

  if (pageInfo) {
    const start = (_currentPage - 1) * PAGE_SIZE + 1;
    const end   = Math.min(_currentPage * PAGE_SIZE, total);
    pageInfo.textContent = `${start}–${end} de ${total}`;
  }

  const btnFirst = document.getElementById("page-first");
  const btnPrev  = document.getElementById("page-prev");
  const btnNext  = document.getElementById("page-next");
  const btnLast  = document.getElementById("page-last");
  if (btnFirst) btnFirst.disabled = _currentPage <= 1;
  if (btnPrev)  btnPrev.disabled  = _currentPage <= 1;
  if (btnNext)  btnNext.disabled  = _currentPage >= totalPages;
  if (btnLast)  btnLast.disabled  = _currentPage >= totalPages;
}

function renderTable(leads) {
  _allLeads    = leads;
  _currentPage = 1;

  if (!leads.length) {
    if (tableBody)  tableBody.innerHTML = "";
    if (emptyState) emptyState.classList.remove("hidden");
    _updatePagination();
    return;
  }
  if (emptyState) emptyState.classList.add("hidden");
  _renderPage(1);
}

// Wire up pagination buttons
document.getElementById("page-first")?.addEventListener("click", () => _renderPage(1));
document.getElementById("page-prev")?.addEventListener("click",  () => _renderPage(_currentPage - 1));
document.getElementById("page-next")?.addEventListener("click",  () => _renderPage(_currentPage + 1));
document.getElementById("page-last")?.addEventListener("click",  () => {
  _renderPage(Math.ceil(_allLeads.length / PAGE_SIZE));
});

async function deleteLead(id, row) {
  const res = await fetch(`/api/leads/${id}`, { method: "DELETE" });
  if (res.ok) {
    _allLeads = _allLeads.filter(l => l.id !== id);
    row.remove();
    const totalPages = Math.ceil(_allLeads.length / PAGE_SIZE);
    // If current page is now empty (we deleted the last item on it) go back one
    if (_currentPage > totalPages && totalPages > 0) _currentPage = totalPages;
    _renderPage(_currentPage);
    if (resultsCount) resultsCount.textContent = `${_allLeads.length} resultados`;
  }
}

async function loadResults(jobId) {
  const res = await fetch(`/api/leads?job_id=${jobId}`);
  if (!res.ok) return;
  const leads = await res.json();
  renderTable(leads);
  if (resultsCount) resultsCount.textContent = `${leads.length} resultados`;
}

// ── Polling ───────────────────────────────────────────────────
function startPolling(jobId) {
  clearInterval(pollInterval);
  pollInterval = setInterval(() => pollJob(jobId), 2000);
}

async function pollJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    updateLoaderCount(job.progress, job.total, job.emails_found, job.waiting_for_proxy, job.proxy_wait_seconds);

    if (job.status === "done" || job.status === "failed") {
      clearInterval(pollInterval);
      hideLoader();
      _scrapingInProgress = false;
      startBtn.disabled = false;
      startBtn.title = "";
      startBtn.innerHTML = 'Iniciar extracción de Google Maps <span class="btn-arrow">→</span>';
      // Refresh capacity after job finishes (proxy states changed)
      loadCapacity();

      if (job.status === "done") {
        await loadResults(jobId);
        if (exportBtn) exportBtn.disabled = false;
      } else {
        showAlert("El scraping falló. Revisa los logs del servidor.");
      }
    }
  } catch (err) {
    console.error("Poll error:", err);
  }
}

// ── Start button ──────────────────────────────────────────────
startBtn.addEventListener("click", async function () {
  hideAlert();

  const query    = document.getElementById("query").value.trim();
  const location = document.getElementById("location").value.trim();
  const maxResults = getCounterValue();

  if (!query) {
    showAlert("Por favor rellena el campo de búsqueda.");
    return;
  }
  if (!location) {
    showAlert("Por favor selecciona una ubicación en el mapa o escribe una ciudad.");
    return;
  }

  _scrapingInProgress = true;
  this.disabled = true;
  this.innerHTML = 'Buscando... <span class="btn-arrow">⏳</span>';
  clearTable();
  showLoader();
  updateLoaderCount(0, 0, 0, false, 0);

  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, location, max_results: maxResults }),
    });

    if (!res.ok) throw new Error(`Error ${res.status}`);
    const data = await res.json();

    currentJobId = data.job_id;
    if (exportBtn) exportBtn.disabled = true;
    startPolling(currentJobId);

  } catch (err) {
    _scrapingInProgress = false;
    showAlert("Error al iniciar la búsqueda: " + err.message);
    this.disabled = false;
    this.title = "";
    this.innerHTML = 'Iniciar extracción de Google Maps <span class="btn-arrow">→</span>';
    hideLoader();
  }
});

// ── Export ────────────────────────────────────────────────────
if (exportBtn) {
  exportBtn.addEventListener("click", () => {
    if (currentJobId) window.location.href = `/api/export/${currentJobId}`;
  });
}
