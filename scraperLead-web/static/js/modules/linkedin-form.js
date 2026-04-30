/**
 * linkedin-form.js
 * Módulo de UI para el LinkedIn Scraper integrado en ScraperLead.
 *
 * Tabs: extraccion | cuentas | historial | contactos
 * API: /api/linkedin/*
 */

export function initLinkedInForm() {
  const page = document.getElementById('linkedin-form-page');
  if (!page) return;

  // ── State ────────────────────────────────────────────────────────────
  let accounts = [];
  let pollTimer = null;
  let contactPage = 1;
  const PER_PAGE = 50;

  // ── DOM refs ──────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const tabBtns  = document.querySelectorAll('.li-nav-tab');
  const sections = document.querySelectorAll('.li-section');

  const statusDot     = $('li-status-dot');
  const statusText    = $('li-status-text');
  const accountsCount = $('li-accounts-count');

  const accountSelect   = $('li-account-select');
  const maxContactsRow   = $('li-max-contacts-row');
  const maxContactsInput = $('li-max-contacts');
  const maxContactsLabel = $('li-max-contacts-label');
  const noAccountWarning = $('li-no-account-warning');
  const modeSection      = $('li-mode-section');
  const alertBox        = $('li-alert');
  const startBtn        = $('li-start-btn');
  const runningBadge    = $('li-running-badge');
  const progressCard    = $('li-progress-card');
  const progressBar     = $('li-progress-bar');
  const progressPct     = $('li-progress-pct');
  const progressLabel   = $('li-progress-label');
  const progressDetail  = $('li-progress-detail');
  const summaryAccount  = $('li-summary-account');
  const summaryPending    = $('li-summary-pending');
  const summaryDone       = $('li-summary-done');
  const summaryErrors     = $('li-summary-errors');
  const summaryTotal      = $('li-summary-total');
  const summaryLastRun    = $('li-summary-last-run');
  const summaryUpdatedAt  = $('li-summary-updated-at');

  const addEmail    = $('li-add-email');
  const addPassword = $('li-add-password');
  const addName     = $('li-add-name');
  const addProxy    = $('li-add-proxy');
  const addAlert    = $('li-add-alert');
  const addBtn      = $('li-add-btn');
  const addStatus   = $('li-add-status');
  const accountsList= $('li-accounts-list');

  const historyBody  = $('li-history-body');
  const reloadHistory= $('li-reload-history');

  const searchInput    = $('li-search-input');
  const filterAccount  = $('li-filter-account');
  const filterType     = $('li-filter-type');
  const exportBtn      = $('li-export-btn');
  const totalCount     = $('li-total-count');
  const emailCount     = $('li-email-count');
  const phoneCount     = $('li-phone-count');
  const contactsTbody  = $('li-contacts-tbody');
  const paginationInfo = $('li-pagination-info');
  const prevBtn        = $('li-prev-btn');
  const nextBtn        = $('li-next-btn');

  // ── Tab navigation ────────────────────────────────────────────────────
  function showTab(sectionName) {
    tabBtns.forEach(btn => {
      const active = btn.dataset.section === sectionName;
      btn.classList.toggle('active', active);
      btn.classList.toggle('text-slate-800', active);
      btn.classList.toggle('font-semibold', active);
      btn.classList.toggle('text-slate-400', !active);
      btn.classList.toggle('font-medium', !active);
    });
    sections.forEach(sec => {
      sec.classList.toggle('active', sec.id === `li-section-${sectionName}`);
    });
    if (sectionName === 'historial') loadHistory();
    if (sectionName === 'contactos') { contactPage = 1; loadContacts(); }
    if (sectionName === 'cuentas') loadAccounts();
  }

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => showTab(btn.dataset.section));
  });

  // ── Mode radio styling + enrich lock ─────────────────────────────────
  function getSelectedAccount() {
    return accounts.find(a => a.username === accountSelect.value) || null;
  }

  // Timestamp de última carga de accounts
  let _lastAccountsLoadTs = null;
  let _updatedAtTimer = null;

  function _startUpdatedAtTick() {
    if (_updatedAtTimer) clearInterval(_updatedAtTimer);
    _updatedAtTimer = setInterval(() => {
      if (!summaryUpdatedAt || _lastAccountsLoadTs === null) return;
      const secs = Math.round((Date.now() - _lastAccountsLoadTs) / 1000);
      summaryUpdatedAt.textContent = secs < 5
        ? 'Actualizado ahora'
        : `Actualizado hace ${secs < 60 ? `${secs}s` : `${Math.floor(secs/60)}m`}`;
    }, 5000);
  }

  function renderAccountSummaryFromAccount(acc) {
    if (!acc) {
      summaryAccount.textContent = 'Sin cuenta seleccionada';
      summaryPending.textContent = '—';
      summaryDone.textContent = '—';
      summaryErrors.textContent = '—';
      summaryTotal.textContent = '—';
      summaryLastRun.textContent = 'Selecciona una cuenta para ver estado de cola y último resumen.';
      if (summaryUpdatedAt) summaryUpdatedAt.textContent = '';
      return;
    }

    summaryAccount.textContent = `@${acc.username}`;
    summaryPending.textContent = Number(acc.queue_pending ?? 0).toLocaleString('es-ES');
    summaryDone.textContent = Number(acc.queue_done ?? 0).toLocaleString('es-ES');
    summaryErrors.textContent = Number(acc.queue_error ?? 0).toLocaleString('es-ES');
    summaryTotal.textContent = Number(acc.queue_total ?? 0).toLocaleString('es-ES');
    if (summaryUpdatedAt && _lastAccountsLoadTs !== null) {
      summaryUpdatedAt.textContent = 'Actualizado ahora';
    }
  }

  function renderRunSummaryFromStatus(s) {
    const lines = [];
    if (s?.mode === 'index') {
      lines.push(`🗂 Índice [@${s.account || '—'}]`);
      if (s.detail) lines.push(s.detail);
      if (typeof s.queue_pending === 'number') lines.push(`📋 Pendientes en cola: ${s.queue_pending}`);
    } else if (s?.mode === 'enrich') {
      lines.push(`📊 Enrich [@${s.account || '—'}]`);
      if (typeof s.new_count === 'number') lines.push(`✅ Nuevos: ${s.new_count}`);
      if (typeof s.updated_count === 'number') lines.push(`🔄 Actualizados: ${s.updated_count}`);
      if (typeof s.skipped_count === 'number') lines.push(`⏭ Saltados (frescos): ${s.skipped_count}`);
      if (typeof s.error_count === 'number') lines.push(`❌ Errores: ${s.error_count}`);
      if (typeof s.queue_pending === 'number') lines.push(`📋 Pendientes en cola: ${s.queue_pending}`);
    }

    if (lines.length) {
      summaryLastRun.textContent = lines.join(' · ');
    }
  }

  function updateModeCards() {
    const selected = document.querySelector('input[name="li-mode"]:checked')?.value;
    const acc = getSelectedAccount();
    const hasAccount = !!accountSelect.value;

    const enrichRadio = document.querySelector('input[name="li-mode"][value="enrich"]');
    const indexRadio  = document.querySelector('input[name="li-mode"][value="index"]');
    const indexCard   = $('li-mode-index-card');
    const enrichCard  = $('li-mode-enrich-card');

    // ── Sin cuenta: bloquear todo ────────────────────────────────────────
    noAccountWarning.classList.toggle('hidden', hasAccount);

    // Deshabilitar radios + bloquear labels (pointer-events en la label evita clicks)
    indexRadio.disabled  = !hasAccount;
    enrichRadio.disabled = !hasAccount;
    indexCard.style.pointerEvents  = hasAccount ? '' : 'none';
    enrichCard.style.pointerEvents = hasAccount ? '' : 'none';
    indexCard.style.opacity  = hasAccount ? '' : '0.4';
    enrichCard.style.opacity = hasAccount ? '' : '0.4';
    startBtn.disabled = !hasAccount;

    if (!hasAccount) {
      maxContactsRow.classList.add('hidden');
      return;
    }
    // Restaurar opacidad cuando hay cuenta (enrich puede sobreescribir después)
    indexCard.style.opacity  = '';
    enrichCard.style.opacity = '';

    // ── Con cuenta: bloquear enrich si nunca indexada ────────────────────
    const neverIndexed = acc && acc.queue_total === 0 && (acc.contacts_total ?? 0) === 0;

    if (neverIndexed) {
      if (selected === 'enrich') {
        indexRadio.checked = true;
      }
      enrichRadio.disabled = true;
      enrichCard.classList.add('opacity-50', 'cursor-not-allowed');
      enrichCard.title = 'Ejecuta primero el modo Index para cargar las conexiones en cola.';
      if (!enrichCard.querySelector('.li-lock-badge')) {
        enrichCard.insertAdjacentHTML('beforeend',
          `<span class="li-lock-badge absolute top-2 right-2 text-[10px] font-semibold bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full">Requiere Index</span>`);
        enrichCard.style.position = 'relative';
      }
    } else {
      enrichRadio.disabled = false;
      enrichCard.classList.remove('opacity-50', 'cursor-not-allowed');
      enrichCard.title = '';
      enrichCard.querySelector('.li-lock-badge')?.remove();
    }


    const currentSelected = document.querySelector('input[name="li-mode"]:checked')?.value;
    indexCard.classList.toggle('border-[#0077B5]', currentSelected === 'index');
    indexCard.classList.toggle('bg-[#0077B5]/5', currentSelected === 'index');
    indexCard.classList.toggle('border-slate-200', currentSelected !== 'index');
    indexCard.classList.toggle('bg-white', currentSelected !== 'index');
    enrichCard.classList.toggle('border-[#0077B5]', currentSelected === 'enrich' && !neverIndexed);
    enrichCard.classList.toggle('bg-[#0077B5]/5', currentSelected === 'enrich' && !neverIndexed);
    enrichCard.classList.toggle('border-slate-200', currentSelected !== 'enrich' || neverIndexed);
    enrichCard.classList.toggle('bg-white', currentSelected !== 'enrich' || neverIndexed);
    maxContactsRow.classList.toggle('hidden', currentSelected !== 'enrich');
  }

  document.querySelectorAll('input[name="li-mode"]').forEach(r => {
    r.addEventListener('change', updateModeCards);
  });
  accountSelect.addEventListener('change', updateModeCards);
  accountSelect.addEventListener('change', () => {
    renderAccountSummaryFromAccount(getSelectedAccount());
  });
  updateModeCards();

  // Clampeo estricto del input de contactos — no se puede superar el cap
  function clampMaxContacts() {
    let val = parseInt(maxContactsInput.value) || 1;
    if (val > maxContactsCap) val = maxContactsCap;
    if (val < 1) val = 1;
    maxContactsInput.value = val;
    const atCap = val >= maxContactsCap;
    maxContactsInput.classList.toggle('border-amber-400', atCap);
    maxContactsInput.classList.toggle('focus:ring-amber-200', atCap);
    maxContactsInput.classList.toggle('border-slate-200', !atCap);
    if (maxContactsLabel) {
      maxContactsLabel.textContent = atCap
        ? `máx. ${maxContactsCap} — límite anti-baneo alcanzado`
        : `máx. ${maxContactsCap} por ejecución (límite anti-baneo)`;
      maxContactsLabel.classList.toggle('text-amber-600', atCap);
      maxContactsLabel.classList.toggle('text-slate-400', !atCap);
    }
  }
  maxContactsInput.addEventListener('input', clampMaxContacts);
  maxContactsInput.addEventListener('blur', clampMaxContacts);

  // ── Health check ──────────────────────────────────────────────────────
  let maxContactsCap = 20;  // default hasta que llegue el health

  async function checkHealth() {
    try {
      const r = await fetch('/api/linkedin/health');
      if (!r.ok) throw new Error();
      const h = await r.json();
      statusDot.className = 'w-2 h-2 rounded-full bg-green-400 shrink-0';
      statusText.textContent = 'Backend activo';
      accountsCount.textContent = h.accounts_count ?? '—';

      // El cap (20) está fijo en el HTML y en maxContactsCap — no se sobreescribe desde el servidor
    } catch {
      statusDot.className = 'w-2 h-2 rounded-full bg-red-400 shrink-0';
      statusText.textContent = 'Sin conexión';
      accountsCount.textContent = '—';
    }
  }

  // ── Load accounts ─────────────────────────────────────────────────────
  async function loadAccounts() {
    try {
      const r = await fetch('/api/linkedin/accounts');
      if (!r.ok) throw new Error();
      accounts = await r.json();
      _lastAccountsLoadTs = Date.now();
      _startUpdatedAtTick();
    } catch {
      accounts = [];
    }

    // Populate selects
    const current = accountSelect.value;
    accountSelect.innerHTML = '<option value="">— Selecciona una cuenta —</option>';
    filterAccount.innerHTML = '<option value="">Todas las cuentas</option>';
    accounts.forEach(acc => {
      const label = acc.display_name || acc.username;
      accountSelect.insertAdjacentHTML('beforeend',
        `<option value="${acc.username}">${label}</option>`);
      filterAccount.insertAdjacentHTML('beforeend',
        `<option value="${acc.username}">${label}</option>`);
    });
    // Restore previous selection explicitly — Safari's dirty-value-flag prevents
    // the 'selected' attribute from taking effect when set via insertAdjacentHTML.
    accountSelect.value = current;

    accountsCount.textContent = accounts.length;
    updateModeCards(); // re-evaluar bloqueo con los datos frescos
    renderAccountSummaryFromAccount(getSelectedAccount());

    // Render accounts list
    if (!accounts.length) {
      accountsList.innerHTML = '<p class="text-sm text-slate-400 text-center py-6">No hay cuentas registradas. Añade una arriba.</p>';
      return;
    }

    accountsList.innerHTML = accounts.map(acc => {
      const sessionOk = acc.session_ok;
      const dotColor = sessionOk === true ? 'bg-green-400' : sessionOk === false ? 'bg-red-400' : 'bg-yellow-400';
      const sessionLabel = sessionOk === true
        ? `Sesión activa (${acc.session_age_days ?? '?'} días)`
        : sessionOk === false ? 'Sin sesión' : 'Sesión desconocida';

      return `
      <div class="flex items-center justify-between gap-4 py-3 border-b border-slate-100 last:border-0">
        <div class="flex items-center gap-3 min-w-0">
          <span class="w-2 h-2 rounded-full ${dotColor} shrink-0"></span>
          <div class="min-w-0">
            <p class="text-sm font-semibold text-slate-800 truncate">${acc.display_name || acc.username}</p>
            <p class="text-xs text-slate-400 truncate">@${acc.username} · ${sessionLabel}</p>
          </div>
        </div>
        <div class="flex items-center gap-4 shrink-0 text-xs text-slate-500">
          <span title="Pendientes en cola">⏳ ${acc.queue_pending}</span>
          <span title="Contactos totales">👥 ${acc.contacts_total}</span>
          <button class="li-delete-account text-red-400 hover:text-red-600 transition"
                  data-username="${acc.username}" title="Eliminar cuenta">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
              <path d="M10 11v6"/><path d="M14 11v6"/>
            </svg>
          </button>
        </div>
      </div>`;
    }).join('');

    document.querySelectorAll('.li-delete-account').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`¿Desactivar la cuenta @${btn.dataset.username}?`)) return;
        await fetch(`/api/linkedin/accounts/${btn.dataset.username}`, { method: 'DELETE' });
        loadAccounts();
        checkHealth();
      });
    });
  }

  // ── Add account ───────────────────────────────────────────────────────
  addBtn.addEventListener('click', async () => {
    hideAddAlert();
    const email = addEmail.value.trim();
    const password = addPassword.value.trim();
    if (!email || !password) {
      showAddAlert('El email y la contraseña son obligatorios.');
      return;
    }
    addBtn.disabled = true;
    addBtn.textContent = 'Iniciando sesión…';
    try {
      const r = await fetch('/api/linkedin/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password,
          display_name: addName.value.trim(),
          proxy: addProxy.value.trim(),
        }),
      });
      const data = await r.json();
      if (!r.ok) {
        showAddAlert(data.detail || 'Error al iniciar sesión.');
      } else {
        addStatus.innerHTML = '✅ Login iniciado en background. Puede tardar 1-2 minutos.<br><span style="color:#b45309">📱 Revisa tu móvil o correo — LinkedIn puede pedir verificación para confirmar el inicio de sesión.</span>';
        addStatus.classList.remove('hidden');
        addEmail.value = addPassword.value = addName.value = addProxy.value = '';
        setTimeout(() => { loadAccounts(); checkHealth(); }, 5000);
      }
    } catch {
      showAddAlert('Error de red al conectar con el backend.');
    }
    addBtn.disabled = false;
    addBtn.textContent = 'Iniciar sesión';
  });

  function showAddAlert(msg) {
    addAlert.textContent = msg;
    addAlert.classList.remove('hidden');
  }
  function hideAddAlert() {
    addAlert.classList.add('hidden');
    addStatus.classList.add('hidden');
  }

  // ── Trigger search ────────────────────────────────────────────────────
  let _sessionWarningShown = false;

  startBtn.addEventListener('click', async () => {
    hideAlert();
    // Limpiar cooldown badge si lo hubiera del modo seleccionado
    if (_cooldownTimer) { clearInterval(_cooldownTimer); _cooldownTimer = null; }
    document.querySelectorAll('.li-cooldown-badge').forEach(b => b.remove());

    const account = accountSelect.value;
    const mode = document.querySelector('input[name="li-mode"]:checked')?.value || 'index';
    const maxContacts = Math.min(parseInt(maxContactsInput.value) || 20, maxContactsCap);

    if (!account) return; // bloqueado por updateModeCards, no debería llegar aquí

    // ── Aviso de sesión expirada (no bloquea, solo informa una vez) ──────
    const acc = getSelectedAccount();
    if (!_sessionWarningShown && acc && acc.session_ok === false) {
      _sessionWarningShown = true;
      showAlert('⚠️ La sesión de esta cuenta puede estar expirada o inválida. El job podría fallar. Pulsa de nuevo para continuar de todas formas.');
      startBtn.disabled = false;
      return;
    }
    _sessionWarningShown = false;

    startBtn.disabled = true;
    runningBadge.classList.add('hidden');
    progressCard.classList.remove('hidden');
    progressLabel.textContent = 'Inicializando…';
    progressBar.style.width = '0%';
    progressPct.textContent = '0%';
    progressDetail.textContent = '';
    try {
      const r = await fetch('/api/linkedin/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, account, max_contacts: maxContacts }),
      });
      const data = await r.json();
      if (!r.ok) {
        if (r.status === 409) {
          // Ya hay un job en curso (doble click u otra pestaña) — mostrar su progreso
          runningBadge.classList.remove('hidden');
          progressCard.classList.remove('hidden');
          progressLabel.textContent = mode === 'index' ? 'Recopilando conexiones…' : 'Enriqueciendo contactos…';
          startPolling();
          return;
        }
        runningBadge.classList.add('hidden');
        progressCard.classList.add('hidden');
        showAlert(humanizeError(data.detail || 'Error al iniciar la extracción.'));
        startBtn.disabled = false;
        if (r.status === 429) startCooldownBadge(mode, data.detail);
        return;
      }
      runningBadge.classList.remove('hidden');
      progressCard.classList.remove('hidden');
      progressLabel.textContent = mode === 'index' ? 'Recopilando conexiones…' : 'Enriqueciendo contactos…';
      startPolling();
    } catch {
      runningBadge.classList.add('hidden');
      showAlert('Error de red al conectar con el backend.');
      startBtn.disabled = false;
    }
  });

  function showAlert(msg) {
    alertBox.textContent = msg;
    alertBox.classList.remove('hidden');
  }
  function hideAlert() {
    alertBox.classList.add('hidden');
  }

  // ── Proactive cooldown badge (mostrado sin pulsar, al cargar cuentas) ─
  function _renderProactiveCooldownBadge(card, remainingSecs, mode) {
    card.querySelector('.li-proactive-cooldown')?.remove();
    if (!remainingSecs || remainingSecs <= 0) return;
    card.style.position = 'relative';
    const m = Math.floor(remainingSecs / 60);
    const s = remainingSecs % 60;
    const label = m > 0 ? `${m}m ${s}s` : `${s}s`;
    card.insertAdjacentHTML('beforeend',
      `<span class="li-proactive-cooldown absolute bottom-2 right-2 text-[10px] font-semibold bg-orange-50 text-orange-600 border border-orange-200 px-2 py-0.5 rounded-full" title="Anti-ban: próximo ${mode} disponible en ${label}">⏱ ${label}</span>`);
  }

  // ── Error hints accionables ───────────────────────────────────────────
  function humanizeError(detail) {
    if (!detail) return 'Error desconocido.';
    if (/session.*expir|no.*session|sesión.*expir/i.test(detail))
      return `${detail} → Ve a "Cuentas" y vuelve a iniciar sesión.`;
    if (/demasiado pronto/i.test(detail))
      return detail;
    if (/presupuesto diario|daily.*budget|max.*contacts.*day/i.test(detail))
      return `${detail} → Espera a mañana o aumenta MAX_CONTACTS_PER_DAY en el .env.`;
    if (/fuera de franja|outside.*window/i.test(detail))
      return `${detail} → El scraping solo opera en la franja horaria configurada.`;
    if (/no.*pending|cola.*vac/i.test(detail))
      return `${detail} → Ejecuta primero el modo Index para cargar conexiones en cola.`;
    if (/ya hay.*scrape|job.*curso|409/i.test(detail))
      return `${detail} → Espera a que termine el job actual o recarga la página.`;
    return detail;
  }

  // ── Cooldown badge (post-429) ─────────────────────────────────────────
  let _cooldownTimer = null;

  function startCooldownBadge(mode, detail) {
    const match = detail && detail.match(/Espera (\d+) min/);
    const waitMin = match ? parseInt(match[1]) : null;
    if (!waitMin) return;

    const card = mode === 'enrich' ? $('li-mode-enrich-card') : $('li-mode-index-card');
    if (!card) return;
    card.style.position = 'relative';

    const existing = card.querySelector('.li-cooldown-badge');
    if (existing) existing.remove();

    let remaining = waitMin * 60;
    const badge = document.createElement('span');
    badge.className = 'li-cooldown-badge absolute top-2 right-2 text-[10px] font-semibold bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full';
    badge.title = 'Anti-ban: intervalo mínimo entre ejecuciones';
    card.appendChild(badge);

    function tick() {
      if (remaining <= 0) {
        badge.remove();
        startBtn.disabled = false;
        return;
      }
      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      badge.textContent = `⏱ ${m}:${String(s).padStart(2, '0')} restante`;
      remaining--;
    }

    tick();
    if (_cooldownTimer) clearInterval(_cooldownTimer);
    _cooldownTimer = setInterval(tick, 1000);
    startBtn.disabled = true;
  }

  // ── Status polling ────────────────────────────────────────────────────
  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 3000);
    pollStatus();
  }

  async function pollStatus() {
    try {
      const r = await fetch('/api/linkedin/status');
      if (!r.ok) return;
      const s = await r.json();
      const percent = typeof s.percent === 'number'
        ? Math.max(0, Math.min(100, s.percent))
        : 0;
      const current = typeof s.current === 'number' ? s.current : null;
      const total = typeof s.total === 'number' ? s.total : null;
      const elapsed = typeof s.elapsed_seconds === 'number' ? s.elapsed_seconds : null;
      const eta = typeof s.eta_seconds === 'number' ? s.eta_seconds : null;

      if (!s.running) {
        clearInterval(pollTimer);
        pollTimer = null;
        runningBadge.classList.add('hidden');
        startBtn.disabled = false;
        if (s.error) {
          progressLabel.textContent = 'Error durante la ejecución';
          progressDetail.textContent = humanizeError(s.detail || s.error);
          progressBar.style.width = '0%';
          progressPct.textContent = '—';
        } else {
          progressLabel.textContent = `✅ ${s.label || 'Completado'}`;
          progressBar.style.width = `${percent || 100}%`;
          progressPct.textContent = `${Math.round(percent || 100)}%`;
          progressDetail.textContent = s.detail || '';
        }
        // Refresh accounts (updates queue stats + re-evaluates enrich lock) then render
        await loadAccounts();
        loadHistory();
        renderRunSummaryFromStatus(s);
      } else {
        progressLabel.textContent = s.label || (s.mode === 'index'
          ? 'Recopilando conexiones…'
          : 'Enriqueciendo contactos…');

        progressBar.style.width = `${percent}%`;
        progressPct.textContent = `${Math.round(percent)}%`;

        const parts = [];
        if (s.account) parts.push(`Cuenta: ${s.account}`);
        if (current !== null && total !== null && total > 0) parts.push(`Progreso: ${current}/${total}`);
        if (typeof s.new_count === 'number') parts.push(`Nuevos: ${s.new_count}`);
        if (typeof s.updated_count === 'number') parts.push(`Actualizados: ${s.updated_count}`);
        if (typeof s.skipped_count === 'number') parts.push(`Saltados: ${s.skipped_count}`);
        if (typeof s.error_count === 'number') parts.push(`Errores: ${s.error_count}`);
        if (elapsed !== null) parts.push(`Tiempo: ${formatDuration(elapsed)}`);
        if (eta !== null && eta > 0) parts.push(`ETA: ${formatDuration(eta)}`);

        if (s.detail) {
          parts.unshift(s.detail);
        }

        progressDetail.textContent = parts.join(' · ');
        renderRunSummaryFromStatus(s);
      }
    } catch {
      // ignore network errors during poll
    }
  }

  // ── History ───────────────────────────────────────────────────────────
  async function loadHistory() {
    historyBody.innerHTML = '<p class="text-sm text-slate-400 text-center py-8">Cargando…</p>';
    try {
      const r = await fetch('/api/linkedin/jobs?limit=50&days=30');
      if (!r.ok) throw new Error();
      const jobs = await r.json();

      if (!jobs.length) {
        historyBody.innerHTML = '<p class="text-sm text-slate-400 text-center py-8">No hay ejecuciones registradas.</p>';
        return;
      }

      historyBody.innerHTML = `
        <table class="w-full text-sm">
          <thead class="bg-slate-50 border-b border-slate-100">
            <tr>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500">Cuenta</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500">Inicio</th>
              <th class="text-left px-4 py-3 text-xs font-semibold text-slate-500">Fin</th>
              <th class="text-right px-4 py-3 text-xs font-semibold text-slate-500">Scrapeados</th>
              <th class="text-right px-4 py-3 text-xs font-semibold text-slate-500">Nuevos</th>
              <th class="text-right px-4 py-3 text-xs font-semibold text-slate-500">Actualizados</th>
            </tr>
          </thead>
          <tbody>
            ${jobs.map(j => `
              <tr class="border-b border-slate-50 hover:bg-slate-50/50">
                <td class="px-4 py-2.5 font-medium text-slate-700">${j.username || '—'}</td>
                <td class="px-4 py-2.5 text-slate-500 text-xs">${formatDate(j.started_at)}</td>
                <td class="px-4 py-2.5 text-slate-500 text-xs">${j.finished_at ? formatDate(j.finished_at) : '—'}</td>
                <td class="px-4 py-2.5 text-right font-semibold text-slate-800">${j.contacts_scraped ?? 0}</td>
                <td class="px-4 py-2.5 text-right text-green-600 font-medium">${j.contacts_new ?? 0}</td>
                <td class="px-4 py-2.5 text-right text-[#0077B5] font-medium">${j.contacts_updated ?? 0}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>`;
    } catch {
      historyBody.innerHTML = '<p class="text-sm text-red-400 text-center py-8">Error al cargar el historial.</p>';
    }
  }

  reloadHistory?.addEventListener('click', loadHistory);

  // ── Contacts ──────────────────────────────────────────────────────────
  let searchDebounce = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => { contactPage = 1; loadContacts(); }, 400);
  });
  filterAccount.addEventListener('change', () => { contactPage = 1; loadContacts(); });
  filterType.addEventListener('change', () => { contactPage = 1; loadContacts(); });
  prevBtn.addEventListener('click', () => { if (contactPage > 1) { contactPage--; loadContacts(); } });
  nextBtn.addEventListener('click', () => { contactPage++; loadContacts(); });

  async function loadContacts() {
    contactsTbody.innerHTML = '<tr><td colspan="6" class="text-center text-slate-400 py-10">Cargando…</td></tr>';
    const params = new URLSearchParams({
      page: contactPage,
      per_page: PER_PAGE,
      search: searchInput.value.trim(),
      account: filterAccount.value,
      filter: filterType.value,
    });

    try {
      const r = await fetch(`/api/linkedin/leads?${params}`);
      if (!r.ok) throw new Error();
      const data = await r.json();

      const contacts = data.contacts || [];
      const total = data.total || 0;
      const pages = data.pages || 1;

      totalCount.textContent = total.toLocaleString('es-ES');
      paginationInfo.textContent = `Página ${contactPage} de ${pages} · ${total.toLocaleString('es-ES')} contactos`;
      prevBtn.disabled = contactPage <= 1;
      nextBtn.disabled = contactPage >= pages;

      // Count with email/phone for stats
      const withEmail = contacts.filter(c => c.emails).length;
      const withPhone = contacts.filter(c => c.phones).length;
      emailCount.textContent = withEmail;
      phoneCount.textContent = withPhone;

      if (!contacts.length) {
        contactsTbody.innerHTML = '<tr><td colspan="6" class="text-center text-slate-400 py-10">No se encontraron contactos.</td></tr>';
        return;
      }

      contactsTbody.innerHTML = contacts.map(c => `
        <tr class="border-b border-slate-50 hover:bg-slate-50/50">
          <td class="px-4 py-2.5">
            ${c.profile_link
              ? `<a href="${escHtml(c.profile_link)}" target="_blank" rel="noopener"
                    class="font-medium text-[#0077B5] hover:underline">${escHtml(c.name || '—')}</a>`
              : `<span class="font-medium text-slate-800">${escHtml(c.name || '—')}</span>`}
          </td>
          <td class="px-4 py-2.5 text-slate-600 text-xs">
            ${escHtml(c.position || '')}${c.position && c.company ? ' · ' : ''}${escHtml(c.company || '') || '—'}
          </td>
          <td class="px-4 py-2.5 text-slate-500 text-xs">${escHtml(c.location || '—')}</td>
          <td class="px-4 py-2.5 text-xs">
            ${c.emails
              ? `<a href="mailto:${escHtml(c.emails)}" class="text-[#0077B5] hover:underline">${escHtml(c.emails)}</a>`
              : '<span class="text-slate-300">—</span>'}
          </td>
          <td class="px-4 py-2.5 text-xs text-slate-600">${escHtml(c.phones || '—')}</td>
          <td class="px-4 py-2.5 text-xs text-slate-400">${formatDate(c.last_scraped_at)}</td>
        </tr>`).join('');
    } catch {
      contactsTbody.innerHTML = '<tr><td colspan="6" class="text-center text-red-400 py-10">Error al cargar contactos.</td></tr>';
    }
  }

  exportBtn.addEventListener('click', () => {
    const params = new URLSearchParams({
      account: filterAccount.value,
      search: searchInput.value.trim(),
      filter: filterType.value,
    });
    window.location.href = `/api/linkedin/leads/export?${params}`;
  });

  // ── Helpers ───────────────────────────────────────────────────────────
  function escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatDate(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
      return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    } catch { return iso; }
  }

  function formatDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds || 0));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    if (m < 60) return `${m}m ${rem}s`;
    const h = Math.floor(m / 60);
    const mRem = m % 60;
    return `${h}h ${mRem}m`;
  }

  // ── Init ──────────────────────────────────────────────────────────────
  checkHealth();
  loadAccounts();

  // Abrir tab desde query param ?tab=contactos (ej. desde /databases)
  const urlTab = new URLSearchParams(window.location.search).get('tab');
  if (urlTab && ['extraccion', 'cuentas', 'historial', 'contactos'].includes(urlTab)) {
    showTab(urlTab);
  }

  // Poll status on load (in case a job was already running)
  pollStatus();
}
