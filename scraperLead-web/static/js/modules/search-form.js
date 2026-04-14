import { emailStatusClass, safeText, toSafeHttpUrl } from '../lib/dom-utils.js';

let _scrapingInProgress = false;

export function isScrapingInProgress() {
  return _scrapingInProgress;
}

function setStartButtonLabel(button, state) {
  if (!button) return;

  const labels = {
    ready: { text: 'Iniciar extracción de Google Maps ', arrow: '→' },
    loading: { text: 'Buscando... ', arrow: '⏳' },
  };
  const selected = labels[state] || labels.ready;
  const arrow = document.createElement('span');
  arrow.className = 'btn-arrow';
  arrow.textContent = selected.arrow;
  button.replaceChildren(document.createTextNode(selected.text), arrow);
}

export function initSearchForm() {
  if (!document.getElementById('map')) return;

  initSearchPage();
}

function initSearchPage() {
  // -- Proxy capacity notice -------------------------------------------------
  let _capacityData = null;

  async function loadCapacity() {
    try {
      const res = await fetch('/api/proxy/capacity');
      if (!res.ok) return;
      _capacityData = await res.json();
      updateCapacityNotice();
    } catch (_) {}
  }

  function updateCapacityNotice() {
    const notice = document.getElementById('capacity-notice');
    if (!notice || !_capacityData || _capacityData.dev_mode) {
      if (notice) notice.classList.add('hidden');
      return;
    }

    const cap = _capacityData;
    const requested = getCounterValue();

    if (cap.daily_remaining === 0) {
      showCapacityNotice('⛔ Límite diario agotado. Podrás volver a scrapear mañana.', 'danger');
      return;
    }
    if (cap.all_in_cooldown) {
      const secs = cap.next_available_seconds;
      const mins = Math.ceil(secs / 60);
      showCapacityNotice(
        `⏸ Todos los proxies en cooldown (~${mins > 1 ? `${mins}min` : `${secs}s`}). El scraping esperará automáticamente.`,
        'warning',
      );
      return;
    }
    if (requested > cap.companies_before_wait) {
      const extra = requested - cap.companies_before_wait;
      const cooldownMin = Math.ceil(cap.cooldown_seconds / 60);
      const cycles = Math.ceil((extra * cap.requests_per_company_estimate) / (cap.requests_available_now || 1));
      const extraMin = cycles * cooldownMin;
      showCapacityNotice(
        `⚠ Con ${requested} empresas el scraping hará ~${cycles} pausa${cycles > 1 ? 's' : ''} de ~${cooldownMin}min (total +${extraMin}min).`,
        'warning',
      );
    } else {
      const remaining = cap.companies_before_wait - requested;
      showCapacityNotice(
        `✓ ${requested} empresas caben sin pausas (margen: ${remaining} más antes de refresco de proxies).`,
        'ok',
      );
    }
  }

  function showCapacityNotice(msg, type) {
    const notice = document.getElementById('capacity-notice');
    if (!notice) return;
    notice.textContent = msg;
    notice.classList.remove('hidden');
    const colorMap = {
      danger: 'bg-red-50 border border-red-200 text-red-700',
      warning: 'bg-amber-50 border border-amber-200 text-amber-700',
      ok: 'bg-green-50 border border-green-200 text-green-700',
    };
    notice.className = `mt-3 text-xs rounded-lg px-3 py-2 ${colorMap[type] || colorMap.warning}`;
  }

  loadCapacity();
  window.setInterval(loadCapacity, 60000);

  // -- State ----------------------------------------------------------------
  let currentJobId = null;
  let pollInterval = null;

  // -- Counter ---------------------------------------------------------------
  const counterInput = document.getElementById('counter-display');
  const counterMinus = document.getElementById('counter-minus');
  const counterPlus = document.getElementById('counter-plus');

  function getCounterValue() {
    return Math.max(1, parseInt(counterInput.value, 10) || 1);
  }

  counterInput.addEventListener('input', function onInput() {
    this.value = this.value.replace(/[^0-9]/g, '');
    updateCapacityNotice();
  });
  counterInput.addEventListener('blur', function onBlur() {
    if (!this.value || parseInt(this.value, 10) < 1) this.value = '1';
    updateCapacityNotice();
  });
  counterInput.addEventListener('paste', function onPaste(event) {
    event.preventDefault();
    const text = (event.clipboardData || window.clipboardData).getData('text');
    const numeric = text.replace(/[^0-9]/g, '');
    if (numeric) this.value = numeric;
    updateCapacityNotice();
  });
  counterMinus.addEventListener('click', () => {
    counterInput.value = Math.max(1, getCounterValue() - 1);
    updateCapacityNotice();
  });
  counterPlus.addEventListener('click', () => {
    counterInput.value = getCounterValue() + 1;
    updateCapacityNotice();
  });

  document.querySelectorAll('.preset-btn').forEach((btn) => {
    btn.addEventListener('click', function onPreset() {
      counterInput.value = this.dataset.value;
      updateCapacityNotice();
    });
  });

  // -- Leaflet Map -----------------------------------------------------------
  const map = L.map('map', { center: [20, 0], zoom: 2, minZoom: 2 });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  let selectedLat = null;
  let selectedLng = null;
  let circleLayer = null;
  let centerMarker = null;

  function getRadiusMeters() {
    return getRadiusKm() * 1000;
  }

  function getRadiusKm() {
    const slider = document.getElementById('radius-slider');
    const value = parseInt(slider.value, 10);
    return Math.min(50, Math.max(1, Number.isNaN(value) ? 10 : value));
  }

  function fitMapToCircle() {
    if (!circleLayer) return;
    map.fitBounds(circleLayer.getBounds().pad(0.2), { animate: true, duration: 0.5 });
  }

  const pinIcon = L.divIcon({
    className: 'map-pin-icon',
    html: `<svg width="28" height="36" viewBox="0 0 28 36" fill="none" xmlns="http://www.w3.org/2000/svg">
    <filter id="shadow" x="-20%" y="-10%" width="140%" height="130%">
      <feDropShadow dx="0" dy="2" stdDeviation="2" flood-opacity="0.25"/>
    </filter>
    <path d="M14 0C6.268 0 0 6.268 0 14C0 24.5 14 36 14 36C14 36 28 24.5 28 14C28 6.268 21.732 0 14 0Z"
          fill="#4285F4" filter="url(#shadow)"/>
    <circle cx="14" cy="14" r="5.5" fill="white"/>
  </svg>`,
    iconSize: [28, 36],
    iconAnchor: [14, 36],
    popupAnchor: [0, -36],
  });

  function setMapLocation(lat, lng) {
    selectedLat = lat;
    selectedLng = lng;
    if (circleLayer) map.removeLayer(circleLayer);
    if (centerMarker) map.removeLayer(centerMarker);
    const radius = getRadiusMeters();
    circleLayer = L.circle([lat, lng], {
      color: '#4285F4',
      fillColor: '#4285F4',
      fillOpacity: 0.1,
      radius,
      weight: 2,
    }).addTo(map);
    centerMarker = L.marker([lat, lng], { draggable: true, icon: pinIcon, zIndexOffset: 1000 }).addTo(map);
    centerMarker.on('drag', (event) => {
      const pos = event.target.getLatLng();
      circleLayer.setLatLng(pos);
      selectedLat = pos.lat;
      selectedLng = pos.lng;
    });
    centerMarker.on('dragend', (event) => {
      const pos = event.target.getLatLng();
      reverseGeocode(pos.lat, pos.lng);
    });
    fitMapToCircle();
  }

  async function geocodeLocation(query) {
    if (!query.trim()) return false;
    try {
      const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1`;
      const res = await fetch(url, { headers: { 'Accept-Language': 'es' } });
      const data = await res.json();
      if (data && data[0]) {
        setMapLocation(parseFloat(data[0].lat), parseFloat(data[0].lon));
        return true;
      }
    } catch (_) {}
    return false;
  }

  async function reverseGeocode(lat, lng) {
    try {
      const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json`;
      const res = await fetch(url, { headers: { 'Accept-Language': 'es' } });
      const data = await res.json();
      if (data && data.address) {
        const a = data.address;
        const name = a.city || a.town || a.village || a.municipality || a.county || data.display_name.split(',')[0];
        document.getElementById('location').value = name;
      }
    } catch (_) {}
  }

  map.on('click', (event) => {
    setMapLocation(event.latlng.lat, event.latlng.lng);
    reverseGeocode(event.latlng.lat, event.latlng.lng);
  });

  document.getElementById('radius-slider').addEventListener('input', function onRadiusInput() {
    const radius = getRadiusKm();
    this.value = String(radius);
    document.getElementById('radius-label').textContent = `${radius} km`;
    if (circleLayer) {
      circleLayer.setRadius(radius * 1000);
      fitMapToCircle();
    }
  });

  document.getElementById('go-btn').addEventListener('click', async () => {
    const loc = document.getElementById('location').value.trim();
    if (loc) await geocodeLocation(loc);
  });

  document.getElementById('location').addEventListener('keydown', async (event) => {
    if (event.key === 'Enter') {
      const loc = document.getElementById('location').value.trim();
      if (loc) await geocodeLocation(loc);
    }
  });

  // -- Alert ----------------------------------------------------------------
  const alertEl = document.getElementById('alert');
  function showAlert(msg) {
    alertEl.textContent = msg;
    alertEl.classList.remove('hidden');
  }
  function hideAlert() {
    alertEl.classList.add('hidden');
  }

  // -- Loader ---------------------------------------------------------------
  const loaderSection = document.getElementById('loader-section');

  function showLoader() {
    loaderSection.classList.remove('hidden');
    loaderSection.style.display = 'flex';
  }
  function hideLoader() {
    loaderSection.classList.add('hidden');
    loaderSection.style.display = '';
  }

  function updateLoaderCount(progress, total, emailsFound, waitingForProxy, proxyWaitSeconds) {
    const loaderCount = document.getElementById('loader-count');
    const proxyWaitEl = document.getElementById('loader-proxy-wait');
    const spinnerEl = document.getElementById('loader-spinner');
    const textEl = document.getElementById('loader-text');

    if (loaderCount) {
      loaderCount.textContent = total > 0
        ? `${progress} / ${total} negocios · ${emailsFound} emails encontrados`
        : 'Conectando con Google Maps...';
    }

    if (waitingForProxy) {
      const secs = proxyWaitSeconds > 0 ? proxyWaitSeconds : '?';
      const mins = proxyWaitSeconds > 60 ? ` (~${Math.ceil(proxyWaitSeconds / 60)}min)` : '';
      if (proxyWaitEl) {
        proxyWaitEl.textContent = `⏸ Proxies en cooldown — reanudando en ~${secs}s${mins}`;
        proxyWaitEl.classList.remove('hidden');
      }
      if (spinnerEl) spinnerEl.classList.add('paused');
      if (textEl) textEl.textContent = 'En pausa...';
    } else {
      if (proxyWaitEl) proxyWaitEl.classList.add('hidden');
      if (spinnerEl) spinnerEl.classList.remove('paused');
      if (textEl) textEl.textContent = 'Cargando emails...';
    }
  }

  // -- Results helpers ------------------------------------------------------
  const tableBody = document.getElementById('results-tbody');
  const emptyState = document.getElementById('empty-state');
  const resultsCount = document.getElementById('results-count');
  const exportBtn = document.getElementById('export-btn');
  const startBtn = document.getElementById('start-btn');

  const PAGE_SIZE = 25;
  let _allLeads = [];
  let _currentPage = 1;

  function clearTable() {
    _allLeads = [];
    _currentPage = 1;
    if (tableBody) tableBody.replaceChildren();
    if (emptyState) emptyState.classList.remove('hidden');
    if (resultsCount) resultsCount.textContent = '';
    updatePagination();
  }

  function buildRow(lead) {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition';
    const makeCell = (className, text, title = null) => {
      const td = document.createElement('td');
      td.className = className;
      td.textContent = text;
      if (title) td.title = title;
      return td;
    };

    const businessName = safeText(lead.business_name);
    const address = safeText(lead.address);
    const phone = safeText(lead.phone);
    const email = safeText(lead.email);
    const emailStatus = safeText(lead.email_status);
    const rating = safeText(lead.rating);

    tr.appendChild(makeCell('px-4 py-3 font-medium text-slate-800 max-w-[160px] truncate', businessName, businessName));
    tr.appendChild(makeCell('px-4 py-3 text-slate-500 max-w-[160px] truncate', address, address));
    tr.appendChild(makeCell('px-4 py-3 text-slate-600', phone));

    const websiteTd = document.createElement('td');
    websiteTd.className = 'px-4 py-3 max-w-[140px] truncate';
    const safeWebsite = toSafeHttpUrl(lead.website);
    if (safeWebsite) {
      const link = document.createElement('a');
      link.href = safeWebsite;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.className = 'text-blue-600 hover:underline text-xs';
      link.textContent = safeText(lead.website);
      websiteTd.appendChild(link);
    } else {
      websiteTd.textContent = '—';
    }
    tr.appendChild(websiteTd);

    const emailTd = makeCell(`px-4 py-3 ${emailStatusClass(lead.email_status)}`, email);
    tr.appendChild(emailTd);
    tr.appendChild(makeCell('px-4 py-3 text-slate-500 text-xs', emailStatus));
    tr.appendChild(makeCell('px-4 py-3 text-slate-600', rating));

    const actionsTd = document.createElement('td');
    actionsTd.className = 'px-4 py-3';
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'w-7 h-7 rounded-lg border border-red-200 text-red-400 hover:bg-red-50 hover:text-red-600 hover:border-red-300 transition text-xs flex items-center justify-center';
    deleteBtn.textContent = '✕';
    deleteBtn.addEventListener('click', () => deleteLead(lead.id, tr));
    actionsTd.appendChild(deleteBtn);
    tr.appendChild(actionsTd);

    return tr;
  }

  function renderPage(page) {
    if (!tableBody) return;
    const total = _allLeads.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    _currentPage = Math.min(Math.max(1, page), totalPages);
    const start = (_currentPage - 1) * PAGE_SIZE;
    const slice = _allLeads.slice(start, start + PAGE_SIZE);
    tableBody.replaceChildren();
    for (const lead of slice) tableBody.appendChild(buildRow(lead));
    updatePagination();
  }

  function updatePagination() {
    const pagination = document.getElementById('pagination');
    const pageInfo = document.getElementById('page-info');
    const total = _allLeads.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (!pagination) return;
    pagination.style.display = total > PAGE_SIZE ? 'flex' : 'none';
    if (pageInfo) {
      const s = (_currentPage - 1) * PAGE_SIZE + 1;
      const e = Math.min(_currentPage * PAGE_SIZE, total);
      pageInfo.textContent = `${s}–${e} de ${total}`;
    }
    const btnFirst = document.getElementById('page-first');
    const btnPrev = document.getElementById('page-prev');
    const btnNext = document.getElementById('page-next');
    const btnLast = document.getElementById('page-last');
    if (btnFirst) btnFirst.disabled = _currentPage <= 1;
    if (btnPrev) btnPrev.disabled = _currentPage <= 1;
    if (btnNext) btnNext.disabled = _currentPage >= totalPages;
    if (btnLast) btnLast.disabled = _currentPage >= totalPages;
  }

  function renderTable(leads) {
    _allLeads = leads;
    _currentPage = 1;
    if (!leads.length) {
      if (tableBody) tableBody.replaceChildren();
      if (emptyState) emptyState.classList.remove('hidden');
      updatePagination();
      return;
    }
    if (emptyState) emptyState.classList.add('hidden');
    renderPage(1);
  }

  document.getElementById('page-first')?.addEventListener('click', () => renderPage(1));
  document.getElementById('page-prev')?.addEventListener('click', () => renderPage(_currentPage - 1));
  document.getElementById('page-next')?.addEventListener('click', () => renderPage(_currentPage + 1));
  document.getElementById('page-last')?.addEventListener('click', () => renderPage(Math.ceil(_allLeads.length / PAGE_SIZE)));

  async function deleteLead(id, row) {
    const res = await fetch(`/api/leads/${id}`, { method: 'DELETE' });
    if (res.ok) {
      _allLeads = _allLeads.filter((lead) => lead.id !== id);
      row.remove();
      const totalPages = Math.ceil(_allLeads.length / PAGE_SIZE);
      if (_currentPage > totalPages && totalPages > 0) _currentPage = totalPages;
      renderPage(_currentPage);
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

  // -- Polling ---------------------------------------------------------------
  function startPolling(jobId) {
    clearInterval(pollInterval);
    pollInterval = window.setInterval(() => pollJob(jobId), 2000);
  }

  async function pollJob(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      updateLoaderCount(job.progress, job.total, job.emails_found, job.waiting_for_proxy, job.proxy_wait_seconds);
      if (job.status === 'done' || job.status === 'failed') {
        clearInterval(pollInterval);
        hideLoader();
        _scrapingInProgress = false;
        startBtn.disabled = false;
        startBtn.title = '';
        setStartButtonLabel(startBtn, 'ready');
        loadCapacity();
        if (job.status === 'done') {
          await loadResults(jobId);
          if (exportBtn) exportBtn.disabled = false;
        } else {
          showAlert('El scraping falló. Revisa los logs del servidor.');
        }
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Poll error:', err);
    }
  }

  // -- Start button ----------------------------------------------------------
  startBtn.addEventListener('click', async function onStart() {
    hideAlert();
    const query = document.getElementById('query').value.trim();
    const location = document.getElementById('location').value.trim();
    const maxResults = getCounterValue();
    const hasCoords = selectedLat !== null && selectedLng !== null;
    const radiusKm = getRadiusKm();

    if (!query) {
      showAlert('Por favor rellena el campo de búsqueda.');
      return;
    }
    if (!location && !hasCoords) {
      showAlert('Por favor selecciona una ubicación en el mapa o escribe una ciudad.');
      return;
    }

    _scrapingInProgress = true;
    this.disabled = true;
    setStartButtonLabel(this, 'loading');
    clearTable();
    showLoader();
    updateLoaderCount(0, 0, 0, false, 0);

    try {
      const payload = { query, location, max_results: maxResults };
      if (hasCoords) {
        payload.lat = selectedLat;
        payload.lng = selectedLng;
        payload.radius_km = radiusKm;
      }

      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      const data = await res.json();
      currentJobId = data.job_id;
      if (exportBtn) exportBtn.disabled = true;
      startPolling(currentJobId);
    } catch (err) {
      _scrapingInProgress = false;
      showAlert(`Error al iniciar la búsqueda: ${err.message}`);
      this.disabled = false;
      this.title = '';
      setStartButtonLabel(this, 'ready');
      hideLoader();
    }
  });

  // -- Export ----------------------------------------------------------------
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      if (currentJobId) window.location.href = `/api/export/${currentJobId}`;
    });
  }
}
