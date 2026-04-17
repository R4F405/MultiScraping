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
  const modeStateInput = document.getElementById('search-mode');
  const modeSingleBtn = document.getElementById('mode-single-btn');
  const modeMultiBtn = document.getElementById('mode-multi-btn');
  const singleModeFields = document.getElementById('single-mode-fields');
  const multiModeFields = document.getElementById('multi-mode-fields');
  const modeButtons = () => Array.from(document.querySelectorAll('.search-mode-btn[data-mode]'));
  const categoryInput = document.getElementById('multi-category-query');
  const categoryDropdown = document.getElementById('category-autocomplete');
  let map = null;
  let categorySuggestions = [];
  let categoryHighlightIndex = -1;
  let categoryDebounce = null;

  // -- Categories catalog sync (manual, from UI) ----------------------------
  const categoriesSyncBtn = document.getElementById('categories-sync-btn');
  const categoriesSyncStatus = document.getElementById('categories-sync-status');
  const categoriesSyncInitialLabel = categoriesSyncBtn?.textContent || 'Actualizar catálogo';
  let categoriesSyncPolling = null;

  async function loadCategoriesCoverageHint() {
    if (!categoriesSyncStatus) return;
    try {
      const res = await fetch('/api/maps/categories/sync/report');
      if (!res.ok) return;
      const data = await res.json();
      const total = Number(data?.catalog_types_count || 0);
      const hybrid = data?.hybrid_summary || {};
      const gbpCount = Number(hybrid?.gbp_categories_count || 0);
      if (total > 0) {
        categoriesSyncStatus.textContent = `Cobertura catálogo: ${total} tipos (GBP: ${gbpCount})`;
      }
    } catch (_) {
      // no-op: hint opcional
    }
  }

  async function pollCategoriesSyncStatus() {
    if (categoriesSyncPolling) return; // evita múltiples loops
    const pollOnce = async () => {
      try {
        const res = await fetch('/api/maps/categories/sync/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const st = await res.json();
        const running = !!st.running;
        const err = st.last_error ? String(st.last_error) : '';

        if (categoriesSyncStatus) {
          if (running) {
            categoriesSyncStatus.textContent = 'Actualizando catálogo...';
          } else if (err) {
            categoriesSyncStatus.textContent = `Error al actualizar: ${err}`;
          } else {
            categoriesSyncStatus.textContent = 'Catálogo actualizado.';
            loadCategoriesCoverageHint();
          }
        }

        if (!running) {
          if (categoriesSyncBtn) {
            categoriesSyncBtn.disabled = false;
            categoriesSyncBtn.textContent = categoriesSyncInitialLabel;
          }
          categoriesSyncPolling = null;
          return;
        }
      } catch (e) {
        if (categoriesSyncStatus) categoriesSyncStatus.textContent = 'Error consultando estado del sync.';
        if (categoriesSyncBtn) {
          categoriesSyncBtn.disabled = false;
          categoriesSyncBtn.textContent = categoriesSyncInitialLabel;
        }
        categoriesSyncPolling = null;
        return;
      }

      categoriesSyncPolling = window.setTimeout(pollOnce, 1500);
    };

    categoriesSyncPolling = window.setTimeout(pollOnce, 0);
  }

  categoriesSyncBtn?.addEventListener('click', async () => {
    if (categoriesSyncBtn?.disabled) return;
    if (categoriesSyncPolling) {
      // Ya hay un sync en progreso o un loop activo.
    }

    if (categoriesSyncBtn) {
      categoriesSyncBtn.disabled = true;
      categoriesSyncBtn.textContent = 'Actualizando...';
    }
    if (categoriesSyncStatus) categoriesSyncStatus.textContent = 'Iniciando actualización...';

    try {
      const res = await fetch('/api/maps/categories/sync', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail ? String(data.detail) : `HTTP ${res.status}`);
      }
      if (categoriesSyncStatus) categoriesSyncStatus.textContent = 'Actualizando catálogo...';
      pollCategoriesSyncStatus();
    } catch (e) {
      if (categoriesSyncStatus) categoriesSyncStatus.textContent = `Error: ${e.message || String(e)}`;
      if (categoriesSyncBtn) {
        categoriesSyncBtn.disabled = false;
        categoriesSyncBtn.textContent = categoriesSyncInitialLabel;
      }
    }
  });
  loadCategoriesCoverageHint();

  function closeCategoryDropdown() {
    if (!categoryDropdown) return;
    categoryDropdown.classList.add('hidden');
    categoryDropdown.replaceChildren();
    categoryDropdown.style.position = '';
    categoryDropdown.style.left = '';
    categoryDropdown.style.top = '';
    categoryDropdown.style.width = '';
    categoryDropdown.style.zIndex = '';
    categorySuggestions = [];
    categoryHighlightIndex = -1;
  }

  function positionCategoryDropdown() {
    if (!categoryDropdown || !categoryInput) return;
    const rect = categoryInput.getBoundingClientRect();
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
    const spaceBelow = Math.max(0, viewportHeight - rect.bottom - 12);
    const spaceAbove = Math.max(0, rect.top - 12);
    const openUpwards = spaceBelow < 220 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(160, Math.min(360, (openUpwards ? spaceAbove : spaceBelow) - 8));

    categoryDropdown.style.position = 'fixed';
    categoryDropdown.style.left = `${Math.round(rect.left)}px`;
    if (openUpwards) {
      const top = Math.max(8, rect.top - maxHeight - 8);
      categoryDropdown.style.top = `${Math.round(top)}px`;
    } else {
      categoryDropdown.style.top = `${Math.round(rect.bottom + 8)}px`;
    }
    categoryDropdown.style.width = `${Math.round(rect.width)}px`;
    categoryDropdown.style.maxHeight = `${Math.round(maxHeight)}px`;
    categoryDropdown.style.zIndex = '80';
  }

  function chooseCategory(index) {
    const selected = categorySuggestions[index];
    if (!selected || !categoryInput) return;
    categoryInput.value = selected.label_es || selected.label_en || '';
    closeCategoryDropdown();
  }

  function renderCategoryDropdown() {
    if (!categoryDropdown) return;
    categoryDropdown.replaceChildren();
    if (!categorySuggestions.length) {
      const empty = document.createElement('div');
      empty.className = 'px-3 py-2 text-xs text-slate-500';
      empty.textContent = 'Sin resultados. Puedes escribir categoría libre.';
      categoryDropdown.appendChild(empty);
      positionCategoryDropdown();
      categoryDropdown.classList.remove('hidden');
      return;
    }

    categorySuggestions.forEach((item, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = `w-full text-left px-3 py-2 text-sm border-b last:border-b-0 border-slate-100 hover:bg-slate-50 ${index === categoryHighlightIndex ? 'bg-blue-50 text-blue-700' : 'text-slate-700'}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', index === categoryHighlightIndex ? 'true' : 'false');
      const primary = document.createElement('div');
      primary.className = 'font-medium';
      primary.textContent = item.label_es || item.label_en || item.type;
      option.appendChild(primary);

      const secondary = document.createElement('div');
      secondary.className = `text-xs mt-0.5 ${index === categoryHighlightIndex ? 'text-blue-600' : 'text-slate-500'}`;
      const enLabel = item.label_en ? String(item.label_en).trim() : '';
      const typeLabel = item.type ? String(item.type).trim() : '';
      const sourceLabel = item.source === 'gbp_category' ? 'GBP category' : 'Maps type';
      secondary.textContent = [enLabel, typeLabel, sourceLabel].filter(Boolean).join(' · ');
      option.appendChild(secondary);
      option.addEventListener('mousedown', (event) => {
        event.preventDefault();
        chooseCategory(index);
      });
      categoryDropdown.appendChild(option);
    });
    positionCategoryDropdown();
    categoryDropdown.classList.remove('hidden');
  }

  async function fetchCategorySuggestions(query, limit = 20) {
    try {
      const url = `/api/maps/categories?q=${encodeURIComponent(query)}&limit=${limit}`;
      const res = await fetch(url);
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data) ? data : [];
    } catch (_) {
      return [];
    }
  }

  function scheduleCategorySearch() {
    if (!categoryInput) return;
    const query = categoryInput.value.trim();
    if (categoryDebounce) window.clearTimeout(categoryDebounce);
    if (categoryDropdown) {
      categoryDropdown.replaceChildren();
      const loading = document.createElement('div');
      loading.className = 'px-3 py-2 text-xs text-slate-500';
      loading.textContent = 'Buscando categorías...';
      categoryDropdown.appendChild(loading);
      positionCategoryDropdown();
      categoryDropdown.classList.remove('hidden');
    }
    categoryDebounce = window.setTimeout(async () => {
      categorySuggestions = await fetchCategorySuggestions(query, 20);
      categoryHighlightIndex = categorySuggestions.length ? 0 : -1;
      renderCategoryDropdown();
    }, 180);
  }

  function getCurrentMode() {
    const raw = modeStateInput?.value || 'single';
    return raw === 'multi_locality' ? 'multi_locality' : 'single';
  }

  function setMode(mode) {
    const nextMode = mode === 'multi_locality' ? 'multi_locality' : 'single';
    if (modeStateInput) modeStateInput.value = nextMode;
    const isMulti = mode === 'multi_locality';
    if (singleModeFields) singleModeFields.classList.toggle('hidden', isMulti);
    if (multiModeFields) multiModeFields.classList.toggle('hidden', !isMulti);

    modeButtons().forEach((button) => {
      const active = button.dataset.mode === nextMode;
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
      button.className = active
        ? 'search-mode-btn px-3 py-2 rounded-lg border border-blue-500 bg-blue-50 text-blue-700 text-sm font-semibold'
        : 'search-mode-btn px-3 py-2 rounded-lg border border-slate-200 bg-white text-slate-600 text-sm font-semibold';
    });

    if (nextMode === 'single' && map) {
      window.setTimeout(() => {
        try {
          const mapEl = document.getElementById('map');
          if (mapEl) {
            // Force layout reflow before invalidating Leaflet size.
            // eslint-disable-next-line no-unused-expressions
            mapEl.offsetHeight;
          }
          map.invalidateSize(true);
          map.invalidateSize(true);
          if (circleLayer) fitMapToCircle();
        } catch (_) {}
      }, 150);
    }

    updateCapacityNotice();
    updateCompaniesTotalSummary();
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('.search-mode-btn[data-mode]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    setMode(button.dataset.mode || 'single');
  });

  // Fallback listeners (in case delegated click is intercepted)
  modeSingleBtn?.addEventListener('click', (event) => {
    event.preventDefault();
    setMode('single');
  });
  modeMultiBtn?.addEventListener('click', (event) => {
    event.preventDefault();
    setMode('multi_locality');
  });
  categoryInput?.addEventListener('focus', async () => {
    // UX acordada: mostrar top relevantes al enfocar.
    categorySuggestions = await fetchCategorySuggestions('', 20);
    categoryHighlightIndex = categorySuggestions.length ? 0 : -1;
    renderCategoryDropdown();
  });
  categoryInput?.addEventListener('click', async () => {
    categorySuggestions = await fetchCategorySuggestions('', 20);
    categoryHighlightIndex = categorySuggestions.length ? 0 : -1;
    renderCategoryDropdown();
  });
  categoryInput?.addEventListener('input', scheduleCategorySearch);
  categoryInput?.addEventListener('keydown', (event) => {
    if (getCurrentMode() !== 'multi_locality') return;
    if (!categorySuggestions.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      categoryHighlightIndex = (categoryHighlightIndex + 1) % categorySuggestions.length;
      renderCategoryDropdown();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      categoryHighlightIndex = (categoryHighlightIndex - 1 + categorySuggestions.length) % categorySuggestions.length;
      renderCategoryDropdown();
    } else if (event.key === 'Enter') {
      if (!categoryDropdown?.classList.contains('hidden') && categoryHighlightIndex >= 0) {
        event.preventDefault();
        chooseCategory(categoryHighlightIndex);
      }
    } else if (event.key === 'Escape') {
      closeCategoryDropdown();
    }
  });

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!target) return;
    const insideInput = categoryInput?.contains(target);
    const insideDropdown = categoryDropdown?.contains(target);
    if (!insideInput && !insideDropdown) closeCategoryDropdown();
  });

  window.addEventListener('resize', () => {
    if (!categoryDropdown || categoryDropdown.classList.contains('hidden')) return;
    positionCategoryDropdown();
  });
  document.addEventListener('scroll', () => {
    if (!categoryDropdown || categoryDropdown.classList.contains('hidden')) return;
    positionCategoryDropdown();
  }, true);

  function parseLocationLines() {
    const textarea = document.getElementById('locations-textarea');
    if (!textarea) return [];
    const lines = textarea.value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    const deduped = [];
    const seen = new Set();
    for (const line of lines) {
      const key = line.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(line);
    }
    return deduped;
  }

  function updateLocationsSummary() {
    const summary = document.getElementById('locations-summary');
    if (!summary) return;
    const textarea = document.getElementById('locations-textarea');
    const count = parseLocationLines().length;
    const maxChars = parseInt(textarea?.getAttribute('maxlength') || '0', 10);
    const currentChars = textarea?.value.length || 0;
    const charsInfo = maxChars > 0 ? ` · ${currentChars}/${maxChars} caracteres` : '';
    summary.textContent = `${count} localidades válidas${charsInfo}`;
  }

  function updateCompaniesTotalSummary() {
    const el = document.getElementById('companies-total-summary');
    if (!el) return;
    const locCount = parseLocationLines().length;
    const perLoc = getCurrentMode() === 'multi_locality' ? getMultiCompaniesTarget() : 0;
    if (!locCount || !perLoc) {
      el.textContent = 'Total a scrapear: —';
      return;
    }
    el.textContent = `Total a scrapear: ${locCount * perLoc} empresas`;
  }

  document.getElementById('locations-textarea')?.addEventListener('input', updateLocationsSummary);
  document.getElementById('locations-textarea')?.addEventListener('input', updateCompaniesTotalSummary);
  document.getElementById('companies-per-location')?.addEventListener('input', () => {
    updateCapacityNotice();
    updateCompaniesTotalSummary();
  });
  updateLocationsSummary();
  updateCompaniesTotalSummary();

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
    const requested = getCurrentMode() === 'multi_locality'
      ? getMultiCompaniesTarget()
      : getCounterValue();

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
    if (!counterInput) return 1;
    return Math.max(1, parseInt(counterInput.value, 10) || 1);
  }

  function getMultiCompaniesTarget() {
    const input = document.getElementById('companies-per-location');
    return Math.max(1, Math.min(200, parseInt(input?.value || '10', 10) || 10));
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
  map = L.map('map', { center: [20, 0], zoom: 2, minZoom: 2 });

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

  window.addEventListener('resize', () => {
    if (!map) return;
    try {
      map.invalidateSize(true);
    } catch (_) {}
  });

  // Initialize mode only after counter and map state are ready.
  setMode('single');

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

  function updateLoaderCount(progress, total, emailsFound, waitingForProxy, proxyWaitSeconds, job = null) {
    const loaderCount = document.getElementById('loader-count');
    const proxyWaitEl = document.getElementById('loader-proxy-wait');
    const spinnerEl = document.getElementById('loader-spinner');
    const textEl = document.getElementById('loader-text');

    if (loaderCount) {
      if (job?.mode === 'multi_locality') {
        const locationLabel = job.current_location_label || '—';
        const locIdx = job.current_location_index || 0;
        const locTotal = job.total_locations || 0;
        const locCompanies = job.current_location_emails_found || 0;
        const locTarget = job.emails_target_per_location || 0;
        const companies = Number(job.progress || 0);
        const emails = Number(job.emails_found || 0);
        loaderCount.textContent = `${locIdx}/${locTotal} · ${locationLabel} · objetivo empresas ${locCompanies}/${locTarget} · ${companies} empresas · ${emails} emails`;
      } else {
        loaderCount.textContent = total > 0
          ? `${progress} / ${total} empresas · ${emailsFound} emails`
          : 'Conectando con Google Maps...';
      }
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
      if (textEl) textEl.textContent = 'Cargando empresas...';
    }
  }

  // -- Results helpers ------------------------------------------------------
  const tableBody = document.getElementById('results-tbody');
  const emptyState = document.getElementById('empty-state');
  const resultsCount = document.getElementById('results-count');
  const exportBtn = document.getElementById('export-btn');
  const startBtn = document.getElementById('start-btn');
  const startBtnMulti = document.getElementById('start-btn-multi');
  const startButtons = [startBtn, startBtnMulti].filter(Boolean);

  const PAGE_SIZE = 25;
  let _allLeads = [];
  let _currentPage = 1;
  let scrapeStartedAtMs = null;

  const progressSection = document.getElementById('progress-section');
  const progressText = document.getElementById('progress-text');
  const progressSubtext = document.getElementById('progress-subtext');
  const progressEta = document.getElementById('progress-eta');
  const progressTotal = document.getElementById('progress-total');
  const progressBarFill = document.getElementById('progress-bar-fill');
  const progressElapsed = document.getElementById('progress-elapsed');

  // -- Job summary ----------------------------------------------------------
  const jobSummarySection = document.getElementById('job-summary-section');
  const jobSummaryTitle = document.getElementById('job-summary-title');
  const jobSummaryCompanies = document.getElementById('job-summary-companies');
  const jobSummaryTargetCompanies = document.getElementById('job-summary-target-companies');
  const jobSummaryEmails = document.getElementById('job-summary-emails');
  const jobSummaryLocations = document.getElementById('job-summary-locations');

  function setJobSummaryVisible(visible) {
    if (!jobSummarySection) return;
    jobSummarySection.classList.toggle('hidden', !visible);
  }

  function updateJobSummaryUI(job, locationsSummary = null) {
    if (!jobSummarySection || !job) return;

    const mode = job.mode || 'single';
    if (mode !== 'multi_locality') return;

    const companiesProcessed = Number(job.progress || 0);
    const totalLoc = Math.max(0, Number(job.total_locations || 0));
    const targetPerLoc = Math.max(1, Number(job.emails_target_per_location || 1));
    const targetTotal = totalLoc * targetPerLoc;
    const emailsFound = Number(job.emails_found || 0);

    if (jobSummaryTitle) {
      jobSummaryTitle.textContent = job.status === 'done'
        ? 'Scrapeo completado'
        : 'Scrapeo finalizado con errores';
    }
    if (jobSummaryCompanies) jobSummaryCompanies.textContent = `${companiesProcessed} empresas`;
    if (jobSummaryTargetCompanies) jobSummaryTargetCompanies.textContent = `${targetTotal} empresas`;
    if (jobSummaryEmails) jobSummaryEmails.textContent = `${emailsFound} emails`;

    if (jobSummaryLocations) {
      if (locationsSummary) {
        const done = Number(locationsSummary.done || 0);
        const empty = Number(locationsSummary.empty || 0);
        const failed = Number(locationsSummary.failed || 0);
        jobSummaryLocations.textContent = `${totalLoc} localidades · ${done} ok · ${empty} vacías · ${failed} fallidas`;
      } else {
        jobSummaryLocations.textContent = `${totalLoc} localidades`;
      }
    }

    setJobSummaryVisible(true);
  }

  const ETA_STATS_KEY = 'maps_scrape_eta_stats_v1';

  function loadEtaStats() {
    try {
      const raw = localStorage.getItem(ETA_STATS_KEY);
      if (!raw) return { single_secs_per_unit: null, multi_secs_per_loc: null };
      const parsed = JSON.parse(raw);
      return {
        single_secs_per_unit: Number(parsed?.single_secs_per_unit) || null,
        multi_secs_per_loc: Number(parsed?.multi_secs_per_loc) || null,
      };
    } catch (_) {
      return { single_secs_per_unit: null, multi_secs_per_loc: null };
    }
  }

  function saveEtaStats(nextStats) {
    try {
      localStorage.setItem(ETA_STATS_KEY, JSON.stringify(nextStats));
    } catch (_) {
      // no-op
    }
  }

  function formatDuration(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  function formatDurationRange(centerSec, uncertaintyRatio = 0.2) {
    const c = Math.max(1, Number(centerSec || 0));
    const u = Math.max(0.05, Math.min(0.5, Number(uncertaintyRatio || 0.2)));
    const minSec = c * (1 - u);
    const maxSec = c * (1 + u);
    return `~${formatDuration(minSec)} - ${formatDuration(maxSec)}`;
  }

  function computeProgressFraction(job) {
    if (!job) return 0;

    if (job.mode === 'multi_locality') {
      const totalLoc = Math.max(0, Number(job.total_locations || 0));
      const currentIndex = Math.max(0, Number(job.current_location_index || 0));
      const target = Math.max(1, Number(job.emails_target_per_location || 1));
      const currentEmails = Math.max(0, Number(job.current_location_emails_found || 0));
      if (totalLoc <= 0) return 0;
      const doneLoc = Math.max(0, currentIndex - 1);
      const currentLocProgress = Math.min(1, currentEmails / target);
      return Math.min(1, (doneLoc + currentLocProgress) / totalLoc);
    }

    const total = Math.max(0, Number(job.total || 0));
    const progress = Math.max(0, Number(job.progress || 0));
    if (total <= 0) return 0;
    return Math.min(1, progress / total);
  }

  function updateProgressUI(job = null) {
    if (!progressSection || !progressBarFill || !progressText || !progressSubtext || !progressEta) return;
    if (!job || !_scrapingInProgress) {
      progressSection.classList.add('hidden');
      progressBarFill.style.width = '0%';
      progressText.textContent = 'Progreso de extracción';
      progressSubtext.textContent = 'Preparando...';
      progressEta.textContent = 'ETA: —';
      if (progressTotal) progressTotal.textContent = 'Total estimado: —';
      if (progressElapsed) progressElapsed.textContent = 'Elapsed: —';
      return;
    }

    progressSection.classList.remove('hidden');
    const fraction = computeProgressFraction(job);
    const percent = Math.round(fraction * 100);
    progressBarFill.style.width = `${percent}%`;

    if (job.mode === 'multi_locality') {
      const idx = Number(job.current_location_index || 0);
      const totalLoc = Number(job.total_locations || 0);
      const label = job.current_location_label || '—';
      const currentCompanies = Number(job.current_location_emails_found || 0);
      const target = Number(job.emails_target_per_location || 0);
      progressText.textContent = `${percent}% · Localidad ${idx}/${totalLoc}`;
      let perLocationEtaText = '';
      if (scrapeStartedAtMs && idx > 1) {
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        const doneLoc = Math.max(1, idx - 1);
        const avgPerLoc = elapsedSec / doneLoc;
        perLocationEtaText = ` · ~${formatDuration(avgPerLoc)} por localidad`;
      }
      const companies = Number(job.progress || 0);
      const emails = Number(job.emails_found || 0);
      progressSubtext.textContent = `${label} · empresas ${currentCompanies}/${target} · ${companies} empresas · ${emails} emails${perLocationEtaText}`;
    } else {
      const progress = Number(job.progress || 0);
      const total = Number(job.total || 0);
      const emails = Number(job.emails_found || 0);
      progressText.textContent = `${percent}% · ${progress}/${total} empresas`;
      progressSubtext.textContent = `${emails} emails`;
    }

    const stats = loadEtaStats();
    let totalEstimateSec = null;
    if (job.mode === 'multi_locality') {
      const totalLoc = Math.max(0, Number(job.total_locations || 0));
      const idx = Math.max(0, Number(job.current_location_index || 0));
      const doneLoc = Math.max(0, idx - 1);
      const currentCompanies = Math.max(0, Number(job.current_location_emails_found || 0));
      const target = Math.max(1, Number(job.emails_target_per_location || 1));

      if (stats.multi_secs_per_loc && totalLoc > 0) {
        totalEstimateSec = stats.multi_secs_per_loc * totalLoc;
      } else if (scrapeStartedAtMs && doneLoc >= 1) {
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        totalEstimateSec = (elapsedSec / doneLoc) * totalLoc;
      } else if (scrapeStartedAtMs && currentCompanies > 0 && totalLoc > 0) {
        // Primera localidad en curso: aproxima por ritmo de empresas actual.
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        const secPerCompany = elapsedSec / currentCompanies;
        totalEstimateSec = secPerCompany * (target * totalLoc);
      }
    } else {
      const total = Math.max(0, Number(job.total || 0));
      const done = Math.max(0, Number(job.progress || 0));
      if (stats.single_secs_per_unit && total > 0) {
        totalEstimateSec = stats.single_secs_per_unit * total;
      } else if (scrapeStartedAtMs && done > 0) {
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        totalEstimateSec = (elapsedSec / done) * total;
      }
    }
    if (progressTotal) {
      if (!totalEstimateSec) {
        progressTotal.textContent = 'Total estimado: calculando...';
      } else {
        let uncertainty = 0.25;
        if (fraction >= 0.6) uncertainty = 0.12;
        else if (fraction >= 0.3) uncertainty = 0.18;

        // Si hay histórico, reducimos un poco la incertidumbre inicial.
        if (job.mode === 'multi_locality' && stats.multi_secs_per_loc) {
          uncertainty = Math.max(0.1, uncertainty - 0.04);
        }
        if (job.mode !== 'multi_locality' && stats.single_secs_per_unit) {
          uncertainty = Math.max(0.1, uncertainty - 0.04);
        }

        progressTotal.textContent = `Total estimado: ${formatDurationRange(totalEstimateSec, uncertainty)}`;
      }
    }

    if (scrapeStartedAtMs && fraction >= 0.03 && fraction < 1) {
      const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
      const remainingSec = (elapsedSec / fraction) - elapsedSec;
      progressEta.textContent = `ETA: ~${formatDuration(remainingSec)}`;
      if (progressElapsed) progressElapsed.textContent = `Elapsed: ${formatDuration(elapsedSec)}`;
    } else if (fraction >= 1 || job.status === 'done') {
      progressEta.textContent = 'ETA: completado';
      if (scrapeStartedAtMs && progressElapsed) {
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        progressElapsed.textContent = `Elapsed: ${formatDuration(elapsedSec)}`;
      }
    } else {
      // Fallback con historial local si aún no hay suficiente avance.
      const stats = loadEtaStats();
      if (job.mode === 'multi_locality') {
        const totalLoc = Math.max(0, Number(job.total_locations || 0));
        const idx = Math.max(0, Number(job.current_location_index || 0));
        const doneLoc = Math.max(0, idx - 1);
        const remainingLoc = Math.max(0, totalLoc - doneLoc);
        if (stats.multi_secs_per_loc && remainingLoc > 0) {
          progressEta.textContent = `ETA: ~${formatDuration(stats.multi_secs_per_loc * remainingLoc)} (histórico)`;
        } else {
          progressEta.textContent = 'ETA: calculando...';
        }
      } else {
        const total = Math.max(0, Number(job.total || 0));
        const done = Math.max(0, Number(job.progress || 0));
        const remaining = Math.max(0, total - done);
        if (stats.single_secs_per_unit && remaining > 0) {
          progressEta.textContent = `ETA: ~${formatDuration(stats.single_secs_per_unit * remaining)} (histórico)`;
        } else {
          progressEta.textContent = 'ETA: calculando...';
        }
      }
      if (scrapeStartedAtMs && progressElapsed) {
        const elapsedSec = (Date.now() - scrapeStartedAtMs) / 1000;
        progressElapsed.textContent = `Elapsed: ${formatDuration(elapsedSec)}`;
      }
    }
  }

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
    const email = lead.email ? safeText(lead.email) : '';
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

  async function loadLocationsSummary(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}/locations`);
      if (!res.ok) return null;
      const rows = await res.json();
      if (!Array.isArray(rows)) return null;
      const counts = { done: 0, empty: 0, failed: 0, running: 0, pending: 0, other: 0 };
      rows.forEach((r) => {
        const st = String(r?.status || 'other');
        if (counts[st] !== undefined) counts[st] += 1;
        else counts.other += 1;
      });
      return counts;
    } catch (_) {
      return null;
    }
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
      updateLoaderCount(job.progress, job.total, job.emails_found, job.waiting_for_proxy, job.proxy_wait_seconds, job);
      updateProgressUI(job);
      if (job.status === 'done' || job.status === 'failed') {
        if (job.mode === 'multi_locality') {
          const locSummary = await loadLocationsSummary(jobId);
          updateJobSummaryUI(job, locSummary);
        }

        const endedAt = Date.now();
        const elapsedSec = scrapeStartedAtMs ? (endedAt - scrapeStartedAtMs) / 1000 : null;
        if (job.status === 'done' && elapsedSec && elapsedSec > 0) {
          const stats = loadEtaStats();
          if (job.mode === 'multi_locality') {
            const totalLoc = Math.max(1, Number(job.total_locations || 1));
            const sample = elapsedSec / totalLoc;
            const prev = stats.multi_secs_per_loc;
            stats.multi_secs_per_loc = prev ? ((prev * 0.7) + (sample * 0.3)) : sample;
          } else {
            const totalUnits = Math.max(1, Number(job.total || 1));
            const sample = elapsedSec / totalUnits;
            const prev = stats.single_secs_per_unit;
            stats.single_secs_per_unit = prev ? ((prev * 0.7) + (sample * 0.3)) : sample;
          }
          saveEtaStats(stats);
        }
        clearInterval(pollInterval);
        hideLoader();
        _scrapingInProgress = false;
        scrapeStartedAtMs = null;
        updateProgressUI(null);
        startButtons.forEach((btn) => {
          btn.disabled = false;
          btn.title = '';
          setStartButtonLabel(btn, 'ready');
        });
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
  async function onStart(event) {
    const clickedBtn = event.currentTarget;
    hideAlert();
    const query = document.getElementById('query').value.trim();
    const location = document.getElementById('location').value.trim();
    const maxResults = getCounterValue();
    const hasCoords = selectedLat !== null && selectedLng !== null;
    const radiusKm = getRadiusKm();

    let payload = null;
    if (getCurrentMode() === 'multi_locality') {
      const category = document.getElementById('multi-category-query')?.value.trim() || '';
      const locations = parseLocationLines();
      const companiesTarget = getMultiCompaniesTarget();
      if (locations.length > 5000) {
        showAlert('Has superado el máximo permitido (5000 localidades por ejecución).');
        return;
      }
      if (!category) {
        showAlert('Por favor rellena la categoría de negocio.');
        return;
      }
      if (!locations.length) {
        showAlert('Pega al menos una localidad (una por línea).');
        return;
      }
      payload = {
        mode: 'multi_locality',
        category_query: category,
        locations,
        emails_target_per_location: companiesTarget,
      };
    } else {
      if (!query) {
        showAlert('Por favor rellena el campo de búsqueda.');
        return;
      }
      if (!location && !hasCoords) {
        showAlert('Por favor selecciona una ubicación en el mapa o escribe una ciudad.');
        return;
      }

      payload = { mode: 'single', query, location, max_results: maxResults };
      if (hasCoords) {
        payload.lat = selectedLat;
        payload.lng = selectedLng;
        payload.radius_km = radiusKm;
      }
    }

    setJobSummaryVisible(false);
    _scrapingInProgress = true;
    scrapeStartedAtMs = Date.now();
    startButtons.forEach((btn) => {
      btn.disabled = true;
      setStartButtonLabel(btn, 'loading');
    });
    clearTable();
    showLoader();
    updateLoaderCount(0, 0, 0, false, 0);
    updateProgressUI({
      status: 'running',
      mode: getCurrentMode(),
      progress: 0,
      total: 0,
      emails_found: 0,
      current_location_index: 0,
      total_locations: 0,
      current_location_label: null,
      current_location_emails_found: 0,
      emails_target_per_location: getMultiCompaniesTarget(),
    });

    try {
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
      scrapeStartedAtMs = null;
      updateProgressUI(null);
      showAlert(`Error al iniciar la búsqueda: ${err.message}`);
      if (clickedBtn) {
        clickedBtn.disabled = false;
        clickedBtn.title = '';
      }
      startButtons.forEach((btn) => {
        btn.disabled = false;
        btn.title = '';
        setStartButtonLabel(btn, 'ready');
      });
      hideLoader();
    }
  }
  startBtn?.addEventListener('click', onStart);
  startBtnMulti?.addEventListener('click', onStart);

  // -- Export ----------------------------------------------------------------
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      if (currentJobId) window.location.href = `/api/export/${currentJobId}`;
    });
  }
}
