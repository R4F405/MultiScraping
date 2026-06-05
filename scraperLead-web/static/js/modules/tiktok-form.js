import { safeText, toSafeHttpUrl } from '../lib/dom-utils.js';

export function initTikTokForm() {
  const page = document.getElementById('tiktok-form-page');
  if (!page) return;
  if (page.dataset.ttInit) return;
  page.dataset.ttInit = '1';

  let poll = null;
  let runningJobId = null;
  let allLeads = [];
  let filteredLeads = [];
  let activeFilters = { hasContact: false };
  let ttView = 'todos';
  let displayedJobId = null;
  let jobsLoaded = false;

  const urlJobId = (() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const v = params.get('job_id');
      return v ? String(v) : null;
    } catch (_) {
      return null;
    }
  })();
  if (urlJobId) {
    ttView = 'scrapeos';
    displayedJobId = urlJobId;
  }

  const views = {
    scraper: document.getElementById('tt-view-scraper'),
    leads: document.getElementById('tt-view-leads'),
  };

  const switchMain = (name) => {
    if (typeof window.ttSwitchMainView === 'function') {
      window.ttSwitchMainView(name);
      return;
    }
    if (views.scraper) views.scraper.classList.toggle('hidden', name !== 'scraper');
    if (views.leads) views.leads.classList.toggle('hidden', name !== 'leads');
  };

  const alertBox = document.getElementById('tt-alert');
  const nicheInput = document.getElementById('tt-niche');
  const locationInput = document.getElementById('tt-location');
  const modeSelect = document.getElementById('tt-mode');
  const modeHelp = document.getElementById('tt-mode-help');
  const emailGoalInput = document.getElementById('tt-email-goal');
  const keywordPresets = Array.from(document.querySelectorAll('.tt-keyword-preset'));
  const startBtn = document.getElementById('tt-start-btn');
  const exportBtn = document.getElementById('tt-export-btn');
  const cancelBtn = document.getElementById('tt-cancel-btn');
  const progressWrap = document.getElementById('tt-progress-wrap');
  const progressBar = document.getElementById('tt-progress-bar');
  const progressText = document.getElementById('tt-progress-text');
  const healthDot = document.getElementById('tt-health-dot');
  const healthText = document.getElementById('tt-health-text');
  const healthDetails = document.getElementById('tt-health-details');
  const engineSummary = document.getElementById('tt-engine-summary');

  const btnTodos = document.getElementById('tt-btn-todos');
  const btnScrapeos = document.getElementById('tt-btn-scrapeos');
  const jobsView = document.getElementById('tt-jobs-view');
  const jobsGrid = document.getElementById('tt-jobs-grid');
  const jobsEmpty = document.getElementById('tt-jobs-empty');
  const jobsError = document.getElementById('tt-jobs-error');
  const jobsCount = document.getElementById('tt-jobs-count');
  const scrapeosPlaceholder = document.getElementById('tt-scrapeos-placeholder');
  const jobDetailHeader = document.getElementById('tt-job-detail-header');
  const jobDetailTitle = document.getElementById('tt-job-detail-title');
  const jobDetailMeta = document.getElementById('tt-job-detail-meta');
  const backBtn = document.getElementById('tt-back-btn');
  const resultsWrapper = document.getElementById('tt-results-wrapper');
  const tableBody = document.getElementById('tt-results-tbody');
  const emptyState = document.getElementById('tt-empty-state');
  const countText = document.getElementById('tt-results-count');
  const filterHasContact = document.getElementById('tt-filter-has-contact');
  const leadsExportBtn = document.getElementById('tt-leads-export-btn');

  const modeHintText = {
    keyword:
      'Modo principal: nicho y, si quieres, ciudad; el servidor recibe nicho|ciudad para priorizar la zona en discovery.',
    hashtag: 'Hashtag en el primer campo; ciudad opcional también se combina como nicho|ciudad.',
  };
  const refreshModeHint = () => {
    if (!modeHelp) return;
    const k = modeSelect?.value || 'keyword';
    modeHelp.textContent = modeHintText[k] || modeHintText.keyword;
  };
  modeSelect?.addEventListener('change', refreshModeHint);
  refreshModeHint();
  const fillPreset = (niche, loc) => {
    if (nicheInput) nicheInput.value = niche;
    if (locationInput) locationInput.value = loc;
    nicheInput?.focus();
  };

  keywordPresets.forEach((btn) => {
    btn.addEventListener('click', () => {
      const niche = String(btn.dataset.niche || btn.dataset.keyword || '').trim();
      const loc = String(btn.dataset.location || '').trim();
      if (modeSelect) modeSelect.value = 'keyword';
      refreshModeHint();
      fillPreset(niche, loc);
    });
  });

  /** @returns {{ target: string } | { error: string }} */
  const buildApiTarget = () => {
    const niche = (nicheInput?.value || '').trim();
    const loc = (locationInput?.value || '').trim();
    if (!niche) return { error: 'Indica un nicho o palabras clave' };
    if (loc) {
      const n = niche.replace(/\|+/g, ' ').replace(/\s+/g, ' ').trim();
      return { target: `${n}|${loc}` };
    }
    return { target: niche };
  };

  const showAlert = (msg, tone = 'error') => {
    if (!alertBox) return;
    const cls =
      tone === 'ok'
        ? 'bg-green-50 border-green-200 text-green-700'
        : tone === 'warn'
          ? 'bg-amber-50 border-amber-200 text-amber-800'
          : 'bg-red-50 border-red-200 text-red-700';
    alertBox.className = `rounded-xl border px-4 py-3 text-sm ${cls}`;
    alertBox.textContent = msg;
    alertBox.classList.remove('hidden');
  };

  const hideAlert = () => alertBox?.classList.add('hidden');
  const stopPoll = () => {
    if (poll) {
      clearInterval(poll);
      poll = null;
    }
  };

  const formatDate = (value) => {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('es-ES');
  };

  const statusLabel = (s) =>
    ({
      completed: 'Completado',
      running: 'En curso',
      failed: 'Error',
      completed_partial: 'Parcial',
      cancelled: 'Cancelado',
      waiting_rate_window: 'Pausado',
      rate_limited: 'Límite',
    }[s] || s);

  const updateHealthUi = (health, proxyStats = null) => {
    const status = health?.status || 'unknown';
    const dotColor =
      status === 'ok' ? 'bg-green-500' : status === 'degraded' ? 'bg-amber-500' : status === 'broken' ? 'bg-red-500' : 'bg-slate-300';
    const statusText =
      status === 'ok'
        ? 'Backend activo'
        : status === 'degraded'
          ? 'Backend activo (sin Playwright)'
          : status === 'broken'
            ? 'Backend con errores'
            : 'Estado desconocido';
    if (healthDot) healthDot.className = `w-2.5 h-2.5 rounded-full ${dotColor} shrink-0`;
    if (healthText) healthText.textContent = statusText;
    const proxies = Number(health?.proxy_count ?? 0);
    const proxyLine = proxies > 0 ? `${proxies} proxies configurados` : 'Sin proxies en health — conexión directa';
    if (healthDetails) healthDetails.textContent = proxyLine;
    const pw = health?.playwright_ready ? 'Playwright listo' : 'solo HTTP/SERP (instala Playwright para más cobertura)';
    const err = health?.last_live_error ? ` · último error job: ${health.last_live_error}` : '';
    const topProxy = Array.isArray(proxyStats?.items) ? proxyStats.items.find((p) => Number(p.ok || 0) > 0) : null;
    const proxyHint = topProxy ? ` · proxy top ${Math.round(Number(topProxy.success_rate || 0) * 100)}%` : '';
    if (engineSummary) engineSummary.textContent = `Datos reales · ${pw}${proxyHint}${err}`;
  };

  const _viewParam = new URLSearchParams(window.location.search).get('view');
  if (_viewParam === 'leads' || urlJobId) switchMain('leads');

  btnTodos?.addEventListener('click', () => switchMain('leads'), true);
  btnScrapeos?.addEventListener('click', () => switchMain('leads'), true);

  function hideJobDetail() {
    jobDetailHeader?.classList.add('hidden');
    jobsView?.classList.remove('hidden');
  }

  function hideJobDetailOnly() {
    jobDetailHeader?.classList.add('hidden');
  }

  function showJobDetail(job) {
    jobsView?.classList.add('hidden');
    if (!jobDetailHeader) return;
    jobDetailHeader.classList.remove('hidden');
    const mode = safeText(job?.mode, '');
    const target = safeText(job?.target, '');
    jobDetailTitle.textContent = `${mode ? `[${mode}] ` : ''}${target || '—'}`;
    const leads = Number(job?.leads_found ?? 0);
    const total = Number(job?.total ?? 0);
    const d = formatDate(job?.started_at);
    jobDetailMeta.textContent = `${statusLabel(job?.status)} · ${leads} leads / ${total} objetivo · ${d}`;
  }

  backBtn?.addEventListener('click', () => {
    displayedJobId = null;
    hideJobDetail();
    setTtViewUi('scrapeos');
    setTtView('scrapeos').catch(() => {});
  });

  window.ttGoToLeads = async () => {
    switchMain('leads');
    await new Promise((r) => setTimeout(r, 40));
    const selected = document.querySelector('#tt-jobs-grid a[data-job-id].ring-2');
    const jobId = selected?.dataset?.jobId;
    if (jobId) {
      try {
        const res = await fetch(`${window.__BASE__}/api/tiktok/jobs/${encodeURIComponent(jobId)}`);
        if (res.ok) showJobDetail(await res.json());
      } catch (_) {}
    }
  };

  const setTtViewUi = (view) => {
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
      hideJobDetailOnly();
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

  const setTtView = async (view) => {
    ttView = view;
    setTtViewUi(view);
    if (view === 'todos') {
      displayedJobId = null;
      await loadResults(null);
      return;
    }
    await renderJobsList();
    if (displayedJobId) {
      await loadResults(displayedJobId);
      try {
        const res = await fetch(`${window.__BASE__}/api/tiktok/jobs/${encodeURIComponent(displayedJobId)}`);
        if (res.ok) showJobDetail(await res.json());
      } catch (_) {}
    }
  };

  const highlightSelectedJob = (jobId) => {
    jobsGrid?.querySelectorAll('a[data-job-id]').forEach((el) => {
      const sel = String(el.dataset.jobId || '') === String(jobId || '');
      el.classList.toggle('border-slate-900', sel);
      el.classList.toggle('ring-2', sel);
      el.classList.toggle('ring-slate-200', sel);
    });
  };

  const renderJobsList = async () => {
    if (!jobsGrid) return;
    jobsError?.classList.add('hidden');
    jobsEmpty?.classList.add('hidden');
    try {
      const res = await fetch(`${window.__BASE__}/api/tiktok/jobs?limit=24`);
      if (!res.ok) throw new Error();
      const jobs = await res.json();
      const arr = Array.isArray(jobs) ? jobs : [];
      if (jobsCount) jobsCount.textContent = arr.length ? `${arr.length} disponibles` : '';
      jobsGrid.replaceChildren();
      if (!arr.length) {
        jobsEmpty?.classList.remove('hidden');
        return;
      }

      const statusBadgeClass = (s) => {
        if (s === 'failed') return 'bg-red-100 text-red-700';
        if (s === 'running') return 'bg-blue-100 text-blue-700';
        if (s === 'completed_partial') return 'bg-amber-100 text-amber-800';
        if (s === 'cancelled') return 'bg-slate-200 text-slate-700';
        return 'bg-green-100 text-green-700';
      };

      for (const job of arr) {
        const jobId = job?.job_id;
        if (!jobId) continue;
        const isSelected = String(jobId) === String(displayedJobId || '');
        const card = document.createElement('a');
        card.href = '#';
        card.dataset.jobId = String(jobId);
        card.className = `bg-white rounded-2xl border border-slate-200 p-5 flex flex-col gap-3 hover:border-slate-400 hover:shadow-sm transition-all no-underline ${
          isSelected ? 'border-slate-900 ring-2 ring-slate-200' : ''
        }`;

        const topRow = document.createElement('div');
        topRow.className = 'flex items-center justify-between';
        const iconWrap = document.createElement('div');
        iconWrap.className = 'w-7 h-7 rounded-lg flex items-center justify-center bg-slate-900 text-white';
        iconWrap.innerHTML =
          '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-2.88 2.5 2.89 2.89 0 0 1-2.89-2.89 2.89 2.89 0 0 1 2.89-2.89c.28 0 .54.04.79.1V9.01a6.29 6.29 0 0 0-.79-.05 6.34 6.34 0 0 0-6.34 6.34 6.34 6.34 0 0 0 6.34 6.34 6.34 6.34 0 0 0 6.33-6.34V8.69a8.18 8.18 0 0 0 4.78 1.52V6.76a4.85 4.85 0 0 1-1.01-.07z"/></svg>';
        const statusBadge = document.createElement('span');
        statusBadge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${statusBadgeClass(job?.status)}`;
        statusBadge.textContent = statusLabel(job?.status);
        topRow.appendChild(iconWrap);
        topRow.appendChild(statusBadge);

        const modeBadge = document.createElement('span');
        modeBadge.className = 'text-xs px-2 py-0.5 rounded-full font-medium bg-slate-100 text-slate-600 self-start';
        modeBadge.textContent = String(job?.mode || '—').toUpperCase();

        const titleDiv = document.createElement('div');
        titleDiv.className = 'font-semibold text-slate-800 text-sm truncate';
        titleDiv.textContent = safeText(job?.target, '—');

        const metaDiv = document.createElement('div');
        metaDiv.className = 'flex items-center justify-between pt-2 border-t border-slate-100 text-xs text-slate-500';
        const leftMeta = document.createElement('div');
        leftMeta.className = 'flex gap-4';
        const totalEl = document.createElement('div');
        totalEl.innerHTML = `<div class="text-base font-semibold text-slate-800">${Number(job?.total ?? 0)}</div><div class="text-[10px] text-slate-400">Objetivo</div>`;
        const leadsEl = document.createElement('div');
        leadsEl.innerHTML = `<div class="text-base font-semibold text-slate-900">${Number(job?.leads_found ?? 0)}</div><div class="text-[10px] text-slate-400">Leads</div>`;
        leftMeta.appendChild(totalEl);
        leftMeta.appendChild(leadsEl);
        const dateDiv = document.createElement('div');
        dateDiv.textContent = formatDate(job?.started_at);
        metaDiv.appendChild(leftMeta);
        metaDiv.appendChild(dateDiv);

        card.appendChild(topRow);
        card.appendChild(modeBadge);
        card.appendChild(titleDiv);
        card.appendChild(metaDiv);

        card.addEventListener('click', async (e) => {
          e.preventDefault();
          displayedJobId = String(jobId);
          ttView = 'scrapeos';
          setTtViewUi('scrapeos');
          await loadResults(displayedJobId);
          highlightSelectedJob(displayedJobId);
          window.ttGoToLeads?.();
        });

        jobsGrid.appendChild(card);
      }
      jobsLoaded = true;
    } catch (_) {
      jobsError?.classList.remove('hidden');
    }
  };

  btnTodos?.addEventListener('click', () => setTtView('todos').catch(() => {}));
  btnScrapeos?.addEventListener('click', () => setTtView('scrapeos').catch(() => {}));

  const hasEmail = (lead) => Boolean(String(lead?.email || '').trim());

  const applyFilters = () => {
    filteredLeads = allLeads.filter((l) => {
      if (activeFilters.hasContact && !hasEmail(l)) return false;
      return true;
    });
    tableBody?.replaceChildren();
    if (!filteredLeads.length) {
      if (allLeads.length && activeFilters.hasContact) {
        emptyState?.classList.remove('hidden');
        if (countText) countText.textContent = '0 con filtros';
      } else {
        emptyState?.classList.add('hidden');
        if (countText) countText.textContent = '';
        if (!allLeads.length) {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td colspan="5" class="text-center text-slate-400 py-10 text-sm">Ve a la vista <strong>Scraper</strong>, inicia una búsqueda y luego vuelve aquí.</td>`;
          tableBody?.appendChild(tr);
        }
      }
      return;
    }
    emptyState?.classList.add('hidden');
    if (countText) countText.textContent = `${filteredLeads.length} resultados`;

    for (const lead of filteredLeads) {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition';

      const handleTd = document.createElement('td');
      handleTd.className = 'px-4 py-3 font-medium text-slate-800 max-w-[140px] truncate';
      const h = safeText(lead.handle, '—');
      const a = document.createElement('a');
      a.href = `https://www.tiktok.com/@${encodeURIComponent(h)}`;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.className = 'text-slate-800 hover:underline';
      a.textContent = h.startsWith('@') ? h : `@${h}`;
      handleTd.appendChild(a);

      const jobShort = lead.job_id ? String(lead.job_id).slice(0, 8) : '—';
      const jobTd = document.createElement('td');
      jobTd.className = 'px-4 py-3 text-slate-500 text-xs font-mono hidden lg:table-cell';
      jobTd.textContent = jobShort;

      const emailTd = document.createElement('td');
      emailTd.className = 'px-4 py-3 text-slate-600 max-w-[180px] truncate';
      emailTd.textContent = safeText(lead.email, '—');

      const webTd = document.createElement('td');
      webTd.className = 'px-4 py-3 max-w-[160px] truncate';
      const safeWeb = toSafeHttpUrl(lead.website);
      if (safeWeb) {
        const wa = document.createElement('a');
        wa.href = safeWeb;
        wa.target = '_blank';
        wa.rel = 'noopener noreferrer';
        wa.className = 'text-blue-600 hover:underline text-xs';
        wa.textContent = safeText(lead.website);
        webTd.appendChild(wa);
      } else {
        webTd.textContent = '—';
      }

      const followersTd = document.createElement('td');
      followersTd.className = 'px-4 py-3 text-slate-500 tabular-nums';
      followersTd.textContent = Number(lead.followers || 0).toLocaleString('es-ES');

      tr.appendChild(handleTd);
      tr.appendChild(jobTd);
      tr.appendChild(emailTd);
      tr.appendChild(webTd);
      tr.appendChild(followersTd);
      tableBody?.appendChild(tr);
    }
  };

  const loadResults = async (jobId) => {
    displayedJobId = jobId ? String(jobId) : null;
    const url = displayedJobId
      ? `${window.__BASE__}/api/tiktok/leads?job_id=${encodeURIComponent(displayedJobId)}`
      : `${window.__BASE__}/api/tiktok/leads`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Error ${r.status}`);
    allLeads = await r.json();
    if (!Array.isArray(allLeads)) allLeads = [];
    applyFilters();
    if (leadsExportBtn) {
      if (allLeads.length > 0) {
        leadsExportBtn.href = displayedJobId
          ? `${window.__BASE__}/api/tiktok/export/${encodeURIComponent(displayedJobId)}`
          : `${window.__BASE__}/api/tiktok/export`;
        leadsExportBtn.classList.remove('hidden');
      } else {
        leadsExportBtn.classList.add('hidden');
      }
    }
  };

  filterHasContact?.addEventListener('change', () => {
    activeFilters.hasContact = Boolean(filterHasContact.checked);
    applyFilters();
  });

  const pollJob = async () => {
    if (!runningJobId) return;
    const r = await fetch(`${window.__BASE__}/api/tiktok/jobs/${encodeURIComponent(runningJobId)}`);
    if (!r.ok) return;
    const job = await r.json();
    const total = Math.max(0, Number(job.total || 0));
    const progress = Math.max(0, Number(job.progress || 0));
    const pct = total > 0 ? Math.round((progress / total) * 100) : 0;
    progressWrap?.classList.remove('hidden');
    if (progressBar) progressBar.style.width = `${Math.min(100, pct)}%`;
    const profilesSeen = Number(job.profiles_seen || 0);
    const emailGoal = Math.max(1, Number(job.email_goal || 1));
    if (progressText) {
      progressText.textContent = `Correos ${progress}/${total || emailGoal} · perfiles:${profilesSeen} · errores:${job.error_count || 0}`;
    }

    if (['completed', 'completed_partial', 'failed', 'cancelled'].includes(job.status)) {
      stopPoll();
      if (startBtn) startBtn.disabled = false;
      if (exportBtn) exportBtn.disabled = false;
      if (cancelBtn) {
        cancelBtn.classList.add('hidden');
        cancelBtn.disabled = true;
      }
      await loadResults(ttView === 'todos' ? null : displayedJobId || runningJobId);
      if (ttView === 'scrapeos') await renderJobsList();
      if (job.status === 'failed') showAlert(job.last_error || 'Job fallido', 'error');
      else if (job.status === 'cancelled') {
        showAlert(job.last_error || 'Scrapeo cancelado. Se conservan los leads obtenidos hasta el momento.', 'warn');
      } else if (job.status === 'completed_partial') {
        const msg = job.last_error || 'Completado parcial (no se alcanzó el objetivo de correos o hubo errores)';
        showAlert(msg, 'warn');
      } else showAlert('Completado', 'ok');
    }
  };

  startBtn?.addEventListener('click', async () => {
    hideAlert();
    const built = buildApiTarget();
    if ('error' in built) {
      showAlert(built.error);
      return;
    }
    const { target } = built;
    startBtn.disabled = true;
    if (exportBtn) exportBtn.disabled = true;
    progressWrap?.classList.remove('hidden');
    if (progressBar) progressBar.style.width = '0%';
    if (progressText) progressText.textContent = 'Inicializando…';
    try {
      const emailGoal = Math.max(1, Math.min(200, Number(emailGoalInput?.value || 1)));
      const maxResults = Math.min(500, Math.max(200, emailGoal * 120));
      const body = {
        mode: modeSelect?.value || 'keyword',
        target,
        max_results: maxResults,
        email_goal: emailGoal,
        enrich_profiles: true,
      };
      const r = await fetch(`${window.__BASE__}/api/tiktok/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${r.status}`);
      }
      const data = await r.json();
      runningJobId = data.job_id;
      stopPoll();
      if (cancelBtn) {
        cancelBtn.classList.remove('hidden');
        cancelBtn.disabled = false;
      }
      poll = window.setInterval(() => {
        pollJob().catch(() => {});
      }, 2000);
      await pollJob();
    } catch (e) {
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) {
        cancelBtn.classList.add('hidden');
        cancelBtn.disabled = true;
      }
      showAlert(`No se pudo iniciar: ${e.message}`);
    }
  });

  cancelBtn?.addEventListener('click', async () => {
    if (!runningJobId) return;
    cancelBtn.disabled = true;
    try {
      const r = await fetch(
        `${window.__BASE__}/api/tiktok/jobs/${encodeURIComponent(runningJobId)}/cancel`,
        { method: 'POST' },
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        showAlert(data.detail || `Error ${r.status}`);
        cancelBtn.disabled = false;
        return;
      }
      if (data.ok === false && data.reason === 'already_finished') {
        showAlert('El scrapeo ya había terminado', 'warn');
        cancelBtn.disabled = false;
        return;
      }
      if (data.ok === false) {
        showAlert('No se pudo cancelar (reinicia el backend TikTok si el job no está en este servidor).', 'warn');
        cancelBtn.disabled = false;
        return;
      }
      showAlert('Cancelando…', 'warn');
    } catch (err) {
      showAlert(String(err?.message || err));
      cancelBtn.disabled = false;
    }
  });

  exportBtn?.addEventListener('click', () => {
    if (runningJobId) window.location.href = `${window.__BASE__}/api/tiktok/export/${encodeURIComponent(runningJobId)}`;
    else window.location.href = `${window.__BASE__}/api/tiktok/export`;
  });

  const initialHealth = page.dataset.health ? JSON.parse(page.dataset.health) : { status: 'unknown' };
  const initialJobs = page.dataset.recentJobs ? JSON.parse(page.dataset.recentJobs) : [];
  updateHealthUi(initialHealth);
  setTtViewUi(ttView);

  (async () => {
    try {
      const [rh, rp] = await Promise.all([
        fetch(`${window.__BASE__}/api/tiktok/health`),
        fetch(`${window.__BASE__}/api/tiktok/proxy-stats`),
      ]);
      const health = rh.ok ? await rh.json() : initialHealth;
      const proxyStats = rp.ok ? await rp.json() : null;
      updateHealthUi(health, proxyStats);
    } catch (_) {
      if (healthText) healthText.textContent = 'Backend no disponible';
    }
  })();

  if (ttView === 'todos') {
    loadResults(null).catch(() => {});
  } else {
    setTtView('scrapeos').catch(() => {});
  }

  const runningJob = initialJobs.find((j) => j.status === 'running' || j.status === 'waiting_rate_window');
  if (runningJob?.job_id) {
    runningJobId = runningJob.job_id;
    progressWrap?.classList.remove('hidden');
    if (cancelBtn) {
      cancelBtn.classList.remove('hidden');
      cancelBtn.disabled = false;
    }
    poll = window.setInterval(() => {
      pollJob().catch(() => {});
    }, 2000);
  }

  window.setInterval(() => {
    if (jobsGrid && ttView === 'scrapeos') renderJobsList().catch(() => {});
  }, 15000);
}
