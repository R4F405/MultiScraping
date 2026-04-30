import { emailStatusClass, safeText, toSafeHttpUrl } from '../lib/dom-utils.js';

export function initInstagramForm() {
  const page = document.getElementById('instagram-form-page');
  if (!page) return;
  if (page.dataset.igFormInit) return; // prevent double-init from app.js + versioned import
  page.dataset.igFormInit = '1';

  // ── State ─────────────────────────────────────────────────────────────
  let dorkingJobId = null;
  let dorkingPollInterval = null;
  let allLeads = [];
  let filteredLeads = [];
  let activeFilters = { hasEmail: false, businessOnly: false };
  let igView = 'todos';
  let displayedJobId = null;
  let jobsLoaded = false;
  let maintenanceMode = false;
  // Start blocked — enabled only after /api/instagram/limits confirms it's safe
  let limitsState = {
    can_start_dorking: false,
    unauth_daily_reached: true,
    used_today_unauth: 0,
  };
  let limitsLoaded = false;

  // Fallback display limits — overridden by actual values from /api/instagram/limits
  const MAX_UNAUTH_DAILY = 500;

  const urlJobId = (() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const v = params.get('job_id');
      return v ? String(v) : null;
    } catch (_) { return null; }
  })();
  if (urlJobId) { igView = 'scrapeos'; displayedJobId = urlJobId; }

  // ── DOM references ────────────────────────────────────────────────────
  const alertEl = document.getElementById('ig-alert');
  const limitAlertEl = document.getElementById('ig-limit-alert');
  const maintenanceBanner = document.getElementById('ig-maintenance-banner');

  // Status row
  const healthDot = document.getElementById('ig-health-dot');
  const healthText = document.getElementById('ig-health-text');
  const healthDetails = document.getElementById('ig-health-details');
  const usageUnauth = document.getElementById('ig-usage-unauth');

  // Tabs
  const tabDorking = document.getElementById('ig-tab-dorking');

  // Help modal
  const helpBtn = document.getElementById('ig-help-btn');
  const helpModal = document.getElementById('ig-help-modal');
  const helpClose = document.getElementById('ig-help-close');
  const helpBackdrop = document.getElementById('ig-help-backdrop');

  // Capacity warning
  const capacityWarning = document.getElementById('ig-capacity-warning');
  const capacityWarningText = document.getElementById('ig-capacity-warning-text');

  // Mode A — dorking
  const nicheInput = document.getElementById('ig-niche');
  const locationInput = document.getElementById('ig-location');
  const dorkingEmailGoal = document.getElementById('ig-dorking-email-goal');
  const dorkingStartBtn = document.getElementById('ig-dorking-start-btn');
  const dorkingExportBtn = document.getElementById('ig-dorking-export-btn');
  const dorkingProgress = document.getElementById('ig-dorking-progress');
  const dorkingBar = document.getElementById('ig-dorking-bar');
  const dorkingProgressText = document.getElementById('ig-dorking-progress-text');
  const dorkingEmailsText = document.getElementById('ig-dorking-emails-text');

  // Results
  const btnTodos = document.getElementById('ig-btn-todos');
  const btnScrapeos = document.getElementById('ig-btn-scrapeos');
  const jobsView = document.getElementById('ig-jobs-view');
  const jobsGrid = document.getElementById('ig-jobs-grid');
  const jobsEmpty = document.getElementById('ig-jobs-empty');
  const jobsError = document.getElementById('ig-jobs-error');
  const jobsCount = document.getElementById('ig-jobs-count');
  const scrapeosPlaceholder = document.getElementById('ig-scrapeos-placeholder');
  const resultsWrapper = document.getElementById('ig-results-wrapper');
  const resultsBody = document.getElementById('ig-results-tbody');
  const resultsCount = document.getElementById('ig-results-count');
  const emptyState = document.getElementById('ig-empty-state');
  const filterHasEmail = document.getElementById('ig-filter-has-email');
  const filterBusiness = document.getElementById('ig-filter-business');
  const leadsExportBtn = document.getElementById('ig-leads-export-btn');

  // ── Helpers ───────────────────────────────────────────────────────────
  const showAlert = (msg, tone = 'error') => {
    const cls = {
      error: 'bg-red-50 border-red-200 text-red-700',
      warn: 'bg-amber-50 border-amber-200 text-amber-800',
      ok: 'bg-green-50 border-green-200 text-green-700',
    };
    alertEl.className = `rounded-xl border px-4 py-3 text-sm ${cls[tone] || cls.error}`;
    alertEl.textContent = msg;
    alertEl.classList.remove('hidden');
  };
  const hideAlert = () => alertEl.classList.add('hidden');
  let _countdownInterval = null;

  const _timeUntilMidnight = () => {
    const now = new Date();
    const midnight = new Date(now);
    midnight.setHours(24, 0, 0, 0);
    const diffMs = midnight - now;
    const h = Math.floor(diffMs / 3_600_000);
    const m = Math.floor((diffMs % 3_600_000) / 60_000);
    const s = Math.floor((diffMs % 60_000) / 1_000);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const hideLimitAlert = () => {
    limitAlertEl?.classList.add('hidden');
    if (_countdownInterval) { clearInterval(_countdownInterval); _countdownInterval = null; }
  };

  const showLimitAlert = (msg, tone = 'warn', showCountdown = false) => {
    if (!limitAlertEl) return;
    const cls = tone === 'error'
      ? 'bg-red-50 border-red-200 text-red-700'
      : 'bg-amber-50 border-amber-200 text-amber-800';
    limitAlertEl.className = `mb-4 rounded-xl border px-4 py-3 text-sm ${cls}`;
    if (_countdownInterval) { clearInterval(_countdownInterval); _countdownInterval = null; }
    if (showCountdown) {
      const span = document.createElement('span');
      limitAlertEl.innerHTML = '';
      limitAlertEl.textContent = msg + ' Reinicio en ';
      limitAlertEl.appendChild(span);
      const update = () => { span.textContent = _timeUntilMidnight(); };
      update();
      _countdownInterval = setInterval(update, 1000);
    } else {
      limitAlertEl.textContent = msg;
    }
    limitAlertEl.classList.remove('hidden');
  };


  const formatDate = (value) => {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString('es-ES');
  };

  const clamp = (val, min, max, fallback) => {
    const n = parseInt(String(val || ''), 10);
    return Number.isNaN(n) ? fallback : Math.max(min, Math.min(max, n));
  };

  if (tabDorking) {
    tabDorking.className = 'px-4 py-2 rounded-lg text-sm font-medium transition bg-white text-slate-800 shadow-sm';
  }

  // ── Health ────────────────────────────────────────────────────────────
  const updateHealthUi = (health) => {
    const status = health?.status || 'unknown';
    const dotColor = status === 'ok' ? 'bg-green-500' : status === 'broken' ? 'bg-red-500' : 'bg-slate-300';
    const statusText = status === 'ok' ? 'Scraper operativo' : status === 'broken' ? 'Scraper con errores' : 'Estado desconocido';
    if (healthDot) healthDot.className = `w-2.5 h-2.5 rounded-full ${dotColor} shrink-0`;
    if (healthText) healthText.textContent = statusText;

    const proxies = Number(health?.proxy_count ?? 0);
    const proxyLine = proxies > 0 ? `${proxies} proxies activos` : 'Sin proxies — conexión directa';
    if (healthDetails) healthDetails.textContent = proxyLine;

    applyMaintenanceMode(Boolean(health?.maintenance_mode || status === 'broken'), health?.message || health?.last_error);
  };

  const updateActionButtons = () => {
    const dorkingBlocked = maintenanceMode || !limitsLoaded || !limitsState.can_start_dorking;
    if (dorkingStartBtn) dorkingStartBtn.disabled = dorkingBlocked;
  };

  const applyMaintenanceMode = (enabled, message) => {
    maintenanceMode = Boolean(enabled);
    if (maintenanceBanner) maintenanceBanner.classList.toggle('hidden', !maintenanceMode);
    if (maintenanceMode && message) {
      showAlert(message, 'warn');
    }
    updateActionButtons();
  };

  // ── Usage stats (read-only, no configuration) ────────────────────────
  const loadUsage = async () => {
    try {
      const res = await fetch('/api/instagram/limits');
      if (!res.ok) return;
      const data = await res.json();
      if (usageUnauth) usageUnauth.textContent = `${data.used_today_unauth ?? '—'}/${data.daily_unauth ?? MAX_UNAUTH_DAILY}`;

      limitsState = {
        can_start_dorking: Boolean(data.can_start_dorking),
        unauth_daily_reached: Boolean(data.unauth_daily_reached),
        used_today_unauth: Number(data.used_today_unauth ?? 0),
      };
      limitsLoaded = true;
      updateActionButtons();
      updateCapacityWarning();

      const blocks = [];
      if (limitsState.unauth_daily_reached) blocks.push('Modo A bloqueado por límite diario');
      if (blocks.length) {
        showLimitAlert(`${blocks.join(' · ')}. `, 'warn', limitsState.unauth_daily_reached);
      } else {
        hideLimitAlert();
      }
    } catch (_) {}
  };

  // ── Progress helpers ──────────────────────────────────────────────────
  const updateProgress = (job, barEl, progressTextEl, emailsTextEl, progressWrapEl) => {
    const total = Math.max(0, Number(job?.total ?? 0));
    const progress = Math.max(0, Number(job?.progress ?? 0));
    const emails = Math.max(0, Number(job?.emails_found ?? 0));
    const pct = total > 0 ? Math.round((progress / total) * 100) : 0;
    if (progressWrapEl) progressWrapEl.classList.remove('hidden');
    if (barEl) barEl.style.width = `${Math.min(100, pct)}%`;
    const enrichAttempts = Math.max(0, Number(job?.enrichment_attempts ?? 0));
    const enrichSuccesses = Math.max(0, Number(job?.enrichment_successes ?? 0));
    const fromIg = Math.max(0, Number(job?.emails_from_ig ?? 0));
    const fromWeb = Math.max(0, Number(job?.emails_from_web ?? 0));
    const profilesChecked = Math.max(0, Number(job?.profiles_checked ?? 0));
    if (progressTextEl) progressTextEl.textContent = `${emails}/${total || '?'} emails · ${profilesChecked} perfiles analizados`;
    if (emailsTextEl) {
      emailsTextEl.textContent = profilesChecked > 0
        ? `Tasa: ${total > 0 ? Math.round((emails / profilesChecked) * 100) : 0}% · IG: ${fromIg} · Web: ${fromWeb}`
        : 'Analizando perfiles…';
    }
  };

  const hideProgress = (wrapEl, barEl) => {
    wrapEl?.classList.add('hidden');
    if (barEl) barEl.style.width = '0%';
  };

  // ── Polling ───────────────────────────────────────────────────────────
  const stopPoll = (intervalRef) => { if (intervalRef) clearInterval(intervalRef); };

  const makePollFn = (jobIdGetter, barEl, progressTextEl, emailsTextEl, progressWrapEl, exportBtnEl) => {
    return async () => {
      const jobId = jobIdGetter();
      if (!jobId) return;
      try {
        const res = await fetch(`/api/instagram/jobs/${encodeURIComponent(jobId)}`);
        if (!res.ok) return;
        const job = await res.json();
        updateProgress(job, barEl, progressTextEl, emailsTextEl, progressWrapEl);

        if (job.status === 'completed') {
          stopPoll(dorkingPollInterval);
          dorkingPollInterval = null;
          hideProgress(progressWrapEl, barEl);
          if (exportBtnEl) exportBtnEl.disabled = false;
          await loadResults(igView === 'todos' ? null : (displayedJobId || jobId));
          // Refresh the jobs list so the completed job appears in Scrapeos section
          if (igView === 'scrapeos') {
            await renderJobsList();
          }
          showAlert('Extracción completada.', 'ok');
          await loadUsage();
        } else if (job.status === 'completed_partial') {
          stopPoll(dorkingPollInterval);
          dorkingPollInterval = null;
          hideProgress(progressWrapEl, barEl);
          if (exportBtnEl) exportBtnEl.disabled = false;
          await loadResults(igView === 'todos' ? null : (displayedJobId || jobId));
          if (igView === 'scrapeos') {
            await renderJobsList();
          }
          const emails = Math.max(0, Number(job?.emails_found ?? 0));
          const total = Math.max(0, Number(job?.total ?? 0));
          const checked = Math.max(0, Number(job?.profiles_checked ?? 0));
          let partialReason = '';
          if (checked === 0 && emails === 0) {
            partialReason = ' El motor de búsqueda (Startpage) bloqueó temporalmente esta IP por exceso de consultas. Espera 2-4 horas o configura proxies para continuar.';
          } else if (checked < 15) {
            partialReason = ' Pool de candidatos agotado — este nicho+ciudad ya fue buscado hoy. Prueba otra ciudad o espera 3 días.';
          } else if (checked > 0 && emails / checked < 0.2) {
            partialReason = ' El nicho tiene pocos emails visibles (~' + Math.round(emails / checked * 100) + '%). Prueba con nutricionistas, psicólogos o dentistas para mayor tasa.';
          } else if (limitsState.used_today_unauth >= 200) {
            partialReason = ' Instagram limita peticiones frecuentes desde la misma IP. Espera 1-2 horas y vuelve a intentarlo.';
          } else {
            partialReason = ' Prueba otra ciudad o espera 3 días para renovar el pool de candidatos.';
          }
          showAlert(`Objetivo no alcanzado: ${emails}/${total || '?'} emails (${checked} perfiles analizados).${partialReason}`, 'warn');
          await loadUsage();
        } else if (job.status === 'waiting_rate_window') {
          const nextRetryRaw = String(job?.next_retry_at || '').trim();
          let retryLabel = 'en unos minutos';
          if (nextRetryRaw) {
            const d = new Date(nextRetryRaw);
            if (!Number.isNaN(d.getTime())) retryLabel = `a las ${d.toLocaleTimeString('es-ES')}`;
          }
          showAlert(`Pausado por límite horario; reanudación automática ${retryLabel}.`, 'warn');
        } else if (job.status === 'rate_limited') {
          stopPoll(dorkingPollInterval);
          dorkingPollInterval = null;
          const emails = Math.max(0, Number(job?.emails_found ?? 0));
          const progress = Math.max(0, Number(job?.progress ?? 0));
          const total = Math.max(0, Number(job?.total ?? 0));
          const reason = safeText(job?.status_detail) || 'límite diario del modo sin sesión';
          showAlert(
            `Extracción detenida por ${reason}. Se han guardado ${emails} emails (${progress}/${total || '?'} perfiles procesados).`,
            'warn',
          );
          await loadUsage();
        } else if (job.status === 'failed') {
          stopPoll(dorkingPollInterval);
          dorkingPollInterval = null;
          showAlert(`Extracción fallida: ${safeText(job?.failure_reason) || 'revisa los logs.'}`, 'error');
        }
      } catch (_) {}
    };
  };

  // ── Mode A — dorking ──────────────────────────────────────────────────
  dorkingStartBtn?.addEventListener('click', async () => {
    if (maintenanceMode) {
      showAlert('Instagram está en mantenimiento temporal.', 'warn');
      return;
    }
    hideAlert();
    const niche = nicheInput?.value.trim();
    const location = locationInput?.value.trim();
    if (!niche) { showAlert('Introduce el nicho (ej: fotógrafo).'); return; }
    if (!location) { showAlert('Introduce la ubicación (ej: Valencia).'); return; }

    if (!limitsState.can_start_dorking) {
      // Re-fetch in real time to rule out stale JS state
      await loadUsage();
    }
    if (!limitsState.can_start_dorking) {
      showAlert(`No puedes iniciar Modo A: se alcanzó el límite diario. Reinicio en ${_timeUntilMidnight()}.`, 'warn');
      return;
    }

    dorkingStartBtn.disabled = true;
    dorkingStartBtn.textContent = 'Buscando...';
    if (dorkingExportBtn) dorkingExportBtn.disabled = true;
    hideProgress(dorkingProgress, dorkingBar);
    allLeads = []; applyFilters();
    if (dorkingPollInterval) { clearInterval(dorkingPollInterval); dorkingPollInterval = null; }

    try {
      const emailGoal = clamp(dorkingEmailGoal?.value, 1, 9999, 20);
      const res = await fetch('/api/instagram/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'dorking', target: `${niche}|${location}`, email_goal: emailGoal }),
      });
      if (!res.ok) {
        let msg = `Error ${res.status}`;
        try {
          const err = await res.json();
          msg = err?.detail || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      const data = await res.json();
      dorkingJobId = data.job_id;
      updateProgress({ progress: 0, total: emailGoal, emails_found: 0 }, dorkingBar, dorkingProgressText, dorkingEmailsText, dorkingProgress);
      dorkingPollInterval = window.setInterval(
        makePollFn(() => dorkingJobId, dorkingBar, dorkingProgressText, dorkingEmailsText, dorkingProgress, dorkingExportBtn),
        2000,
      );
    } catch (err) {
      showAlert(`No se pudo iniciar el dorking: ${err.message}`);
    } finally {
      dorkingStartBtn.disabled = false;
      dorkingStartBtn.textContent = 'Buscar con Dorking';
      updateActionButtons();
    }
  });

  dorkingExportBtn?.addEventListener('click', () => exportLeads(dorkingJobId));

  // ── Export ────────────────────────────────────────────────────────────
  const exportLeads = (jobId) => {
    if (!filteredLeads.length) return;
    const cols = ['Username','Nombre','Email','Estado email','Seguidores','Tipo cuenta','Fuente email','Origen','Web'];
    const esc = (v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`;
    const rows = filteredLeads.map((l) => [
      l.username, l.full_name, l.email, l.email_status,
      l.follower_count ?? l.followers_count ?? 0,
      Boolean(l.is_business) ? 'Business' : 'Personal',
      l.email_source, l.source_type, l.website,
    ].map(esc).join(','));
    const blob = new Blob(['\uFEFF' + [cols.join(','), ...rows].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ig_leads_${jobId || 'todos'}_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ── Results table ─────────────────────────────────────────────────────
  const buildRow = (lead) => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition';

    const cell = (cls, text, title = null) => {
      const td = document.createElement('td');
      td.className = cls;
      td.textContent = text;
      if (title) td.title = title;
      return td;
    };

    const username = safeText(lead.username);
    const fullName = safeText(lead.full_name);
    const email = safeText(lead.email);
    const followersValue = Number(lead.followers_count ?? lead.follower_count ?? 0);
    const followers = Number.isFinite(followersValue)
      ? followersValue.toLocaleString('es-ES') : '0';

    const usernameTd = document.createElement('td');
    usernameTd.className = 'px-4 py-3 font-medium text-slate-800 max-w-[160px] truncate';
    const usernameLink = document.createElement('a');
    usernameLink.href = `https://www.instagram.com/${encodeURIComponent(username)}/`;
    usernameLink.target = '_blank';
    usernameLink.rel = 'noopener noreferrer';
    usernameLink.className = 'text-slate-800 hover:text-purple-700 hover:underline';
    usernameLink.textContent = username;
    usernameLink.title = username;
    usernameTd.appendChild(usernameLink);
    // Column order: Usuario | Nombre | Email | Seguidores | Tipo | Fuente | Web
    tr.appendChild(usernameTd);
    tr.appendChild(cell('px-4 py-3 text-slate-600 max-w-[200px] truncate', fullName, fullName));
    tr.appendChild(cell(`px-4 py-3 ${emailStatusClass(lead.email_status)}`, email));
    tr.appendChild(cell('px-4 py-3 text-slate-600 tabular-nums', followers));

    const bizTd = document.createElement('td');
    bizTd.className = 'px-4 py-3';
    const badge = document.createElement('span');
    const isBiz = Boolean(lead.is_business);
    badge.className = isBiz
      ? 'inline-flex items-center text-xs font-medium bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full'
      : 'inline-flex items-center text-xs font-medium bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full';
    badge.textContent = isBiz ? 'Business' : 'Personal';
    bizTd.appendChild(badge);
    tr.appendChild(bizTd);

    tr.appendChild(cell('px-4 py-3 text-slate-500 text-xs', safeText(lead.email_source) || '—'));

    const webTd = document.createElement('td');
    webTd.className = 'px-4 py-3 max-w-[160px] truncate';
    const safeWeb = toSafeHttpUrl(lead.website);
    if (safeWeb) {
      const a = document.createElement('a');
      a.href = safeWeb; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.className = 'text-blue-600 hover:underline text-xs';
      a.textContent = safeText(lead.website);
      webTd.appendChild(a);
    } else { webTd.textContent = '—'; }
    tr.appendChild(webTd);

    return tr;
  };

  const applyFilters = () => {
    filteredLeads = allLeads.filter((l) => {
      if (activeFilters.hasEmail && !String(l.email || '').trim()) return false;
      if (activeFilters.businessOnly && !Boolean(l.is_business)) return false;
      return true;
    });
    resultsBody?.replaceChildren();
    if (filteredLeads.length) {
      emptyState?.classList.add('hidden');
      for (const lead of filteredLeads) resultsBody?.appendChild(buildRow(lead));
    } else {
      emptyState?.classList.remove('hidden');
    }
    if (resultsCount) resultsCount.textContent = filteredLeads.length ? `${filteredLeads.length} resultados` : '';
  };

  const loadResults = async (jobId) => {
    displayedJobId = jobId ? String(jobId) : null;
    const url = displayedJobId
      ? `/api/instagram/leads?job_id=${encodeURIComponent(displayedJobId)}`
      : '/api/instagram/leads';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Error ${res.status}`);
    allLeads = await res.json();
    applyFilters();
    if (leadsExportBtn) {
      if (allLeads.length > 0) {
        leadsExportBtn.href = displayedJobId
          ? `/api/instagram/export/${encodeURIComponent(displayedJobId)}`
          : '/api/instagram/export';
        leadsExportBtn.classList.remove('hidden');
      } else {
        leadsExportBtn.classList.add('hidden');
      }
    }
  };

  // ── Jobs view ─────────────────────────────────────────────────────────
  const setIgViewUi = (view) => {
    const isTodos = view === 'todos';
    if (btnTodos) {
      btnTodos.className = `flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition ${
        isTodos ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
      }`;
    }
    if (btnScrapeos) {
      btnScrapeos.className = `flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition ${
        !isTodos ? 'bg-white text-slate-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
      }`;
    }
    jobsView?.classList.toggle('hidden', isTodos);

    if (isTodos) {
      scrapeosPlaceholder?.classList.add('hidden');
      resultsWrapper?.classList.remove('hidden');
      return;
    }
    if (!displayedJobId) {
      scrapeosPlaceholder?.classList.remove('hidden');
      resultsWrapper?.classList.add('hidden');
      highlightSelectedJob(null);
    } else {
      scrapeosPlaceholder?.classList.add('hidden');
      resultsWrapper?.classList.remove('hidden');
    }
  };

  const setIgView = async (view) => {
    igView = view;
    setIgViewUi(view);
    if (view === 'todos') {
      displayedJobId = null;
      await loadResults(null);
      return;
    }
    await renderJobsList();
    if (displayedJobId) await loadResults(displayedJobId);
  };

  const highlightSelectedJob = (jobId) => {
    jobsGrid?.querySelectorAll('a[data-job-id]').forEach((el) => {
      const sel = String(el.dataset.jobId || '') === String(jobId || '');
      el.classList.toggle('border-purple-300', sel);
      el.classList.toggle('ring-2', sel);
      el.classList.toggle('ring-purple-100', sel);
    });
  };

  // Render jobs list into the grid
  const renderJobsList = async () => {
    if (!jobsGrid) return;
    jobsError?.classList.add('hidden');
    jobsEmpty?.classList.add('hidden');
    try {
      const res = await fetch('/api/instagram/jobs?limit=24');
      if (!res.ok) throw new Error();
      const jobs = await res.json();
      const arr = Array.isArray(jobs) ? jobs : [];
      if (jobsCount) jobsCount.textContent = arr.length ? `${arr.length} disponibles` : '';
      jobsGrid.replaceChildren();
      if (!arr.length) { jobsEmpty?.classList.remove('hidden'); return; }

      const statusBadgeClass = (s) => {
        if (s === 'failed') return 'bg-red-100 text-red-700';
        if (s === 'running') return 'bg-blue-100 text-blue-700';
        if (s === 'waiting_rate_window') return 'bg-amber-100 text-amber-800';
        if (s === 'rate_limited') return 'bg-amber-100 text-amber-800';
        if (s === 'completed_partial') return 'bg-amber-100 text-amber-800';
        return 'bg-green-100 text-green-700';
      };
      const statusLabel = (s) => {
        if (s === 'failed') return 'Error';
        if (s === 'running') return 'En curso';
        if (s === 'waiting_rate_window') return 'Pausado';
        if (s === 'rate_limited') return 'Límite';
        if (s === 'completed_partial') return 'Parcial';
        return 'Completado';
      };
      const modeColor = () => 'bg-orange-100 text-orange-600';
      const modeLabel = () => 'Dorking';

      for (const job of arr) {
        const jobId = job?.job_id;
        if (!jobId) continue;
        const isSelected = String(jobId) === String(displayedJobId || '');
        const card = document.createElement('a');
        card.href = '#';
        card.dataset.jobId = String(jobId);
        card.className = `bg-white rounded-2xl border border-slate-200 p-5 flex flex-col gap-3 hover:border-purple-300 hover:shadow-sm transition-all no-underline ${isSelected ? 'border-purple-300 ring-2 ring-purple-100' : ''}`;

        const mode = safeText(job?.mode, '');
        const target = safeText(job?.target, '');
        const displayTarget = mode === 'dorking'
          ? target.replace('|', ' · ')
          : `@${target.replace(/^@/, '')}`;

        const topRow = document.createElement('div');
        topRow.className = 'flex items-center justify-between';

        const iconWrap = document.createElement('div');
        iconWrap.className = `w-7 h-7 rounded-lg flex items-center justify-center ${modeColor(mode)}`;
        iconWrap.innerHTML = mode === 'dorking'
          ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>`
          : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><circle cx="12" cy="12" r="4"/></svg>`;

        const statusBadge = document.createElement('span');
        statusBadge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${statusBadgeClass(job?.status)}`;
        statusBadge.textContent = statusLabel(job?.status);
        topRow.appendChild(iconWrap);
        topRow.appendChild(statusBadge);

        const modeBadge = document.createElement('span');
        modeBadge.className = `text-xs px-2 py-0.5 rounded-full font-medium self-start ${modeColor(mode)}`;
        modeBadge.textContent = modeLabel(mode);

        const titleDiv = document.createElement('div');
        titleDiv.className = 'font-semibold text-slate-800 text-sm truncate';
        titleDiv.textContent = displayTarget || '—';

        const metaDiv = document.createElement('div');
        metaDiv.className = 'flex items-center justify-between pt-2 border-t border-slate-100 text-xs text-slate-500';
        const leftMeta = document.createElement('div');
        leftMeta.className = 'flex gap-4';
        const totalEl = document.createElement('div');
        totalEl.innerHTML = `<div class="text-base font-semibold text-slate-800">${Number(job?.total ?? 0)}</div><div class="text-[10px] text-slate-400">Objetivo</div>`;
        const emailsEl = document.createElement('div');
        emailsEl.innerHTML = `<div class="text-base font-semibold text-purple-700">${Number(job?.emails_found ?? 0)}</div><div class="text-[10px] text-slate-400">Emails</div>`;
        leftMeta.appendChild(totalEl);
        leftMeta.appendChild(emailsEl);
        const dateDiv = document.createElement('div');
        dateDiv.textContent = formatDate(job?.started_at || job?.created_at);
        metaDiv.appendChild(leftMeta);
        metaDiv.appendChild(dateDiv);

        card.appendChild(topRow);
        card.appendChild(modeBadge);
        card.appendChild(titleDiv);
        card.appendChild(metaDiv);

        card.addEventListener('click', async (e) => {
          e.preventDefault();
          displayedJobId = String(jobId);
          igView = 'scrapeos';
          setIgViewUi('scrapeos');
          await loadResults(displayedJobId);
          highlightSelectedJob(displayedJobId);
          // Navigate to Leads section in the new 4-section nav
          window.igGoToLeads?.();
        });

        jobsGrid.appendChild(card);
      }
      jobsLoaded = true;
    } catch (_) {
      jobsError?.classList.remove('hidden');
    }
  };

  btnTodos?.addEventListener('click', () => setIgView('todos').catch(() => {}));
  btnScrapeos?.addEventListener('click', () => setIgView('scrapeos').catch(() => {}));

  // Called by the section nav (instagram.html inline script) when switching sections
  window.igOnSectionChange = (sectionName) => {
    if (sectionName === 'scrapeos' && !jobsLoaded) {
      renderJobsList().catch(() => {});
    }
  };

  filterHasEmail?.addEventListener('change', () => { activeFilters.hasEmail = filterHasEmail.checked; applyFilters(); });
  filterBusiness?.addEventListener('change', () => { activeFilters.businessOnly = filterBusiness.checked; applyFilters(); });

  // ── Initial boot ──────────────────────────────────────────────────────
  const initialHealth = page.dataset.health ? JSON.parse(page.dataset.health) : { status: 'unknown' };
  const initialJobs = page.dataset.recentJobs ? JSON.parse(page.dataset.recentJobs) : [];

  updateHealthUi(initialHealth);
  hideLimitAlert();
  setIgViewUi(igView);

  // Load state in parallel
  (async () => {
    await Promise.allSettled([
      loadUsage(),
      (async () => {
        try {
          const res = await fetch('/api/instagram/health');
          if (res.ok) updateHealthUi(await res.json());
        } catch (_) {}
      })(),
    ]);
  })();

  // Initial results
  if (igView === 'todos') {
    loadResults(null).catch(() => {});
  } else {
    renderJobsList().catch(() => {});
    if (displayedJobId) loadResults(displayedJobId).catch(() => {});
  }

  // Attach to running job if any
  const runningJob = initialJobs.find((j) => j.status === 'running' || j.status === 'waiting_rate_window');
  if (runningJob?.job_id) {
    const mode = runningJob.mode;
    if (mode === 'dorking') {
      dorkingJobId = runningJob.job_id;
      dorkingProgress?.classList.remove('hidden');
      dorkingPollInterval = window.setInterval(
        makePollFn(() => dorkingJobId, dorkingBar, dorkingProgressText, dorkingEmailsText, dorkingProgress, dorkingExportBtn),
        2000,
      );
    }
  }

  // ── Help modal ────────────────────────────────────────────────────────
  const openHelp = () => helpModal?.classList.remove('hidden');
  const closeHelp = () => helpModal?.classList.add('hidden');
  helpBtn?.addEventListener('click', openHelp);
  helpClose?.addEventListener('click', closeHelp);
  helpBackdrop?.addEventListener('click', closeHelp);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeHelp(); });

  // ── Capacity warning (shown when goal may exceed available candidates) ─
  const updateCapacityWarning = () => {
    if (!capacityWarning || !capacityWarningText) return;
    const goal = parseInt(dorkingEmailGoal?.value ?? '20', 10);
    const usedToday = limitsState.used_today_unauth;

    let msg = '';
    if (usedToday >= 200 && goal > 30) {
      msg = `Ya llevas ${usedToday} peticiones a Instagram hoy. Con muchas búsquedas seguidas el rendimiento baja — considera esperar 1-2h o reducir el objetivo.`;
    } else if (goal >= 80) {
      msg = `Para ${goal} emails usa ciudades distintas en cada búsqueda (ej: Madrid → Sevilla → Valencia) para evitar que el pool de candidatos se agote.`;
    } else if (goal >= 50) {
      msg = `Para objetivos grandes, si ya buscaste este nicho hoy los resultados pueden ser menores. Los perfiles sin email se renuevan cada 3 días.`;
    }

    if (msg) {
      capacityWarningText.textContent = msg;
      capacityWarning.classList.remove('hidden');
    } else {
      capacityWarning.classList.add('hidden');
    }
  };

  dorkingEmailGoal?.addEventListener('input', updateCapacityWarning);

  // Refresh limits every 30s
  window.setInterval(loadUsage, 30000);

  // Retry loadUsage quickly if backend was slow to start (e.g. after startall.sh)
  // Attempts: 2s, 5s, 10s — stops as soon as limitsLoaded becomes true
  const _retryDelays = [2000, 5000, 10000];
  _retryDelays.forEach((delay) => {
    window.setTimeout(async () => { if (!limitsLoaded) await loadUsage(); }, delay);
  });

  // Refresh jobs list periodically so new completed jobs appear in Scrapeos history
  window.setInterval(() => {
    if (jobsGrid && igView === 'scrapeos') {
      renderJobsList().catch(() => {});
    }
  }, 15000);
}
