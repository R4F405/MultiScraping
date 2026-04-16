import { safeText, toSafeHttpUrl } from '../lib/dom-utils.js';

export function initTikTokForm() {
  const page = document.getElementById('tiktok-form-page');
  if (!page) return;

  // ── State ─────────────────────────────────────────────────────────────
  let activeSection = 'extraccion'; // 'extraccion' | 'scrapeos' | 'leads'
  let currentJobId = null;
  let pollInterval = null;
  let allLeads = [];
  let jobsLoaded = false;
  let limitsState = {
    can_start: true,
    daily_reached: false,
    hourly_reached: false,
  };

  const urlJobId = (() => {
    try {
      const params = new URLSearchParams(window.location.search);
      const v = params.get('job_id');
      return v ? String(v) : null;
    } catch (_) { return null; }
  })();
  if (urlJobId) { activeSection = 'scrapeos'; currentJobId = urlJobId; }

  // ── DOM references ────────────────────────────────────────────────────
  // Alert
  const alertEl = document.getElementById('tt-alert');

  // Tabs
  const tabExtraccion = document.querySelector('[data-section="extraccion"]');
  const tabScrapeos = document.querySelector('[data-section="scrapeos"]');
  const tabLeads = document.querySelector('[data-section="leads"]');

  // Forms and inputs
  const targetInput = document.getElementById('tt-target');
  const emailGoalInput = document.getElementById('tt-email-goal');
  const minFollowersInput = document.getElementById('tt-min-followers');
  const startBtn = document.getElementById('tt-start-btn');

  // Progress
  const progressBox = document.getElementById('tt-progress-box');
  const progressBar = document.getElementById('tt-progress-bar');
  const statusLabel = document.getElementById('tt-status-label');
  const statusDetail = document.getElementById('tt-status-detail');
  const emailsBadge = document.getElementById('tt-emails-badge');
  const exportBox = document.getElementById('tt-export-box');
  const exportBtn = document.getElementById('tt-export-btn');
  const exportCount = document.getElementById('tt-export-count');

  // Jobs list
  const jobsList = document.getElementById('tt-jobs-list');
  const refreshJobsBtn = document.getElementById('tt-refresh-jobs-btn');

  // Leads table
  const leadsTbody = document.getElementById('tt-leads-tbody');
  const leadsCount = document.getElementById('tt-leads-count');
  const exportAllBtn = document.getElementById('tt-export-all-btn');

  // Status pills
  const pillDaily = document.getElementById('tt-pill-daily');
  const pillHourly = document.getElementById('tt-pill-hourly');
  const dotDaily = document.getElementById('tt-dot-daily');
  const dotHourly = document.getElementById('tt-dot-hourly');

  // ── Helpers ───────────────────────────────────────────────────────────
  const showAlert = (msg, tone = 'error', details = null) => {
    const cls = {
      error: 'bg-red-50 border-red-200 text-red-700',
      warn: 'bg-amber-50 border-amber-200 text-amber-800',
      ok: 'bg-green-50 border-green-200 text-green-700',
    };
    if (!alertEl) return;

    let fullMsg = msg;
    if (details) {
      fullMsg = `<strong>${msg}</strong><br/><span class="text-xs opacity-90 mt-2 block">${details}</span>`;
    }

    alertEl.className = `rounded-xl border px-4 py-3 text-sm ${cls[tone] || cls.error}`;
    if (details) {
      alertEl.innerHTML = fullMsg;
    } else {
      alertEl.textContent = msg;
    }
    alertEl.classList.remove('hidden');
  };

  const hideAlert = () => alertEl?.classList.add('hidden');

  // Error messages with context and actions
  const errorMessages = {
    no_target: {
      msg: 'Introduce un hashtag o keyword',
      details: 'Ejemplos: #fotógrafo, diseñador barcelona, coach online, diseño gráfico.',
    },
    daily_limit: {
      msg: '⏳ Límite diario alcanzado (200 requests/día)',
      details: 'Has usado todos los requests del día. El contador se resetea a las 00:00. Intenta mañana o espera hasta entonces.',
      tone: 'warn',
    },
    hourly_limit: {
      msg: '⏳ Límite por hora alcanzado (40 requests/hora)',
      details: 'Has hecho muchas búsquedas en poco tiempo. TikTok temporalmente no permite más. Espera 1 hora y reintenra.',
      tone: 'warn',
    },
    rate_limited: {
      msg: '🚫 TikTok está bloqueando las búsquedas',
      details: 'TikTok detectó actividad anómala de tu IP. Esto es temporal. Intenta en 2-4 horas o cambia de red (móvil).',
      tone: 'warn',
    },
    browser_blocked: {
      msg: '🤖 TikTok detectó el scraper como bot',
      details: 'Playwright fue bloqueado por anti-bot de TikTok. Esto es temporal. Intenta en 30 minutos. Si persiste, reinicia el servidor: ./start_all.sh',
      tone: 'warn',
    },
    no_server: {
      msg: '❌ No se puede conectar con TikTokLeads',
      details: 'El servidor backend no está activo en puerto 8004. Ejecuta: ./start_all.sh en una terminal.',
      tone: 'error',
    },
    network_error: {
      msg: '❌ Error de conexión de red',
      details: 'No hay conexión al servidor. Verifica que puedas acceder a http://localhost:8004. Si el problema persiste, reinicia el navegador.',
      tone: 'error',
    },
    job_failed: {
      msg: '❌ La extracción falló',
      details: 'Ocurrió un error interno durante el scraping. Posibles causas: TikTok bloqueó la IP, el navegador se cerró, o timeout. Intenta de nuevo en unos minutos.',
      tone: 'error',
    },
    proxy_error: {
      msg: '⚠️ Problema con la conexión proxy',
      details: 'Si tienes un proxy configurado, verifica que sea válido. Edita tiktokleads/.env y configura TIKTOK_PROXY_URL correctamente.',
      tone: 'error',
    },
    db_error: {
      msg: '❌ Error en la base de datos',
      details: 'La base de datos está corrupta o no se puede acceder. Elimina tiktokleads/data/tiktokleads.db y reinicia el servidor.',
      tone: 'error',
    },
    timeout: {
      msg: '⏱️ Timeout: la búsqueda tardó demasiado',
      details: 'TikTok tardó más de 60 segundos en responder. Esto puede indicar: red lenta, TikTok está lento, o bloqueo. Intenta con menos followers mínimos.',
      tone: 'warn',
    },
  };

  const clamp = (val, min, max, fallback) => {
    const n = parseInt(String(val || ''), 10);
    return Number.isNaN(n) ? fallback : Math.max(min, Math.min(max, n));
  };

  const formatDate = (value) => {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString('es-ES');
  };

  // ── Tab navigation ────────────────────────────────────────────────────
  const setActiveSection = (section) => {
    activeSection = section;
    document.querySelectorAll('.tt-section').forEach((s) => {
      s.classList.toggle('active', s.id === `tt-section-${section}`);
    });
    document.querySelectorAll('[data-section]').forEach((tab) => {
      const isActive = tab.dataset.section === section;
      tab.classList.toggle('active', isActive);
      if (isActive) {
        tab.classList.remove('text-slate-400', 'hover:text-slate-600');
        tab.classList.add('text-slate-800');
      } else {
        tab.classList.remove('text-slate-800');
        tab.classList.add('text-slate-400', 'hover:text-slate-600');
      }
    });

    if (section === 'scrapeos' && !jobsLoaded) {
      loadJobsList().catch(() => {});
    }
    if (section === 'leads') {
      loadLeads().catch(() => {});
    }
  };

  [tabExtraccion, tabScrapeos, tabLeads].forEach((tab) => {
    tab?.addEventListener('click', () => {
      if (tab.dataset.section) setActiveSection(tab.dataset.section);
    });
  });

  // ── Limits and status ─────────────────────────────────────────────────
  const updateLimitsUi = (data) => {
    const daily = data?.requests_today ?? 0;
    const hourly = data?.requests_this_hour ?? 0;
    const maxDaily = data?.max_daily ?? 200;
    const maxHourly = data?.max_per_hour ?? 40;

    if (pillDaily) pillDaily.textContent = `${daily}/${maxDaily} hoy`;
    if (pillHourly) pillHourly.textContent = `${hourly}/${maxHourly} /h`;

    // Color status dots based on usage
    const dailyPct = Math.round((daily / maxDaily) * 100);
    const hourlyPct = Math.round((hourly / maxHourly) * 100);

    const getColor = (pct) => {
      if (pct >= 80) return 'bg-red-500';
      if (pct >= 50) return 'bg-yellow-400';
      return 'bg-green-500';
    };

    if (dotDaily) {
      dotDaily.className = `w-1.5 h-1.5 rounded-full ${getColor(dailyPct)}`;
    }
    if (dotHourly) {
      dotHourly.className = `w-1.5 h-1.5 rounded-full ${getColor(hourlyPct)}`;
    }

    limitsState = {
      can_start: dailyPct < 100 && hourlyPct < 100,
      daily_reached: dailyPct >= 100,
      hourly_reached: hourlyPct >= 100,
    };
    startBtn.disabled = !limitsState.can_start;
  };

  const loadLimits = async () => {
    try {
      const res = await fetch('/api/tiktok/limits');
      if (!res.ok) {
        console.warn('Error loading limits:', res.status);
        return;
      }
      const data = await res.json();
      updateLimitsUi(data);
    } catch (err) {
      console.error('Failed to load limits:', err);
      // Silencioso, solo log. No mostrar alert en background task.
    }
  };

  // ── Progress helpers ──────────────────────────────────────────────────
  const updateProgress = (job) => {
    const total = Math.max(0, Number(job?.total ?? 0));
    const progress = Math.max(0, Number(job?.profiles_scanned ?? 0));
    const emails = Math.max(0, Number(job?.emails_found ?? 0));
    const pct = total > 0 ? Math.round((progress / total) * 100) : 0;

    if (progressBox) progressBox.classList.remove('hidden');
    if (progressBar) progressBar.style.width = `${Math.min(100, pct)}%`;
    if (emailsBadge) emailsBadge.textContent = `${emails} emails`;

    const fromBio = Math.max(0, Number(job?.emails_from_bio ?? 0));
    const fromWeb = Math.max(0, Number(job?.emails_from_web ?? 0));
    const skipped = Math.max(0, Number(job?.skipped_count ?? 0));

    if (statusLabel) statusLabel.textContent = job?.status_detail || 'Procesando...';
    if (statusDetail) {
      statusDetail.textContent = `${progress}/${total} perfiles · Bio: ${fromBio} · Web: ${fromWeb} · Omitidos: ${skipped}`;
    }
  };

  const hideProgress = () => progressBox?.classList.add('hidden');

  // ── Polling ───────────────────────────────────────────────────────────
  const stopPoll = () => { if (pollInterval) clearInterval(pollInterval); pollInterval = null; };

  const startPolling = () => {
    stopPoll();
    pollInterval = window.setInterval(async () => {
      if (!currentJobId) { stopPoll(); return; }
      try {
        const res = await fetch(`/api/tiktok/jobs/${encodeURIComponent(currentJobId)}`);
        if (!res.ok) return;
        const job = await res.json();
        updateProgress(job);

        if (job.status === 'completed' || job.status === 'completed_partial') {
          stopPoll();
          if (exportBtn) exportBtn.disabled = false;
          if (exportBox) exportBox.classList.remove('hidden');
          if (exportCount) {
            const count = Number(job?.emails_found ?? 0);
            exportCount.textContent = count ? `${count} emails para exportar` : '';
          }
          await loadLeads();
          await loadJobsList();

          const emailsFound = Number(job?.emails_found ?? 0);
          const profilesScanned = Number(job?.profiles_scanned ?? 0);
          const fromBio = Number(job?.emails_from_bio ?? 0);
          const fromWeb = Number(job?.emails_from_web ?? 0);

          if (job.status === 'completed') {
            const details = `${emailsFound} emails de ${profilesScanned} perfiles (${fromBio} de bio, ${fromWeb} de web). Haz clic en [Leads] para ver la tabla o [Exportar CSV] para descargar.`;
            showAlert('✅ Extracción completada exitosamente', 'ok', details);
          } else {
            const details = `Objetivo no alcanzado: ${emailsFound}/${job?.total ?? 0} emails. Pero se guardaron los ${emailsFound} encontrados. Haz clic en [Leads] para ver o [Exportar CSV].`;
            showAlert('⚠️ Objetivo parcialmente alcanzado', 'warn', details);
          }
          await loadLimits();
        } else if (job.status === 'rate_limited') {
          stopPoll();
          const reason = safeText(job?.failure_reason) || 'límite de TikTok';
          let errKey = 'rate_limited';
          if (reason.includes('hourly') || reason.includes('hora')) {
            errKey = 'hourly_limit';
          } else if (reason.includes('daily') || reason.includes('día')) {
            errKey = 'daily_limit';
          }
          const errInfo = errorMessages[errKey];
          const emails = job?.emails_found ?? 0;
          const detail = `Se guardaron ${emails} emails antes de pausar. ${errInfo.details}`;
          showAlert(errInfo.msg, errInfo.tone || 'warn', detail);
          await loadLimits();
        } else if (job.status === 'failed') {
          stopPoll();
          const reason = safeText(job?.failure_reason) || 'error_desconocido';
          let errKey = 'job_failed';

          // Detectar el tipo de error por el reason
          if (reason.includes('bot') || reason.includes('detected')) {
            errKey = 'browser_blocked';
          } else if (reason.includes('proxy')) {
            errKey = 'proxy_error';
          } else if (reason.includes('database') || reason.includes('db')) {
            errKey = 'db_error';
          } else if (reason.includes('timeout')) {
            errKey = 'timeout';
          }

          const errInfo = errorMessages[errKey] || { msg: 'Extracción fallida', details: reason };
          showAlert(errInfo.msg, 'error', errInfo.details);
          await loadLimits();
        }
      } catch (_) {}
    }, 2000);
  };

  // ── Start extraction ──────────────────────────────────────────────────
  startBtn?.addEventListener('click', async () => {
    hideAlert();
    const target = targetInput?.value.trim();
    if (!target) {
      const err = errorMessages.no_target;
      showAlert(err.msg, 'warn', err.details);
      return;
    }

    if (!limitsState.can_start) {
      const isDaily = limitsState.daily_reached;
      const err = isDaily ? errorMessages.daily_limit : errorMessages.hourly_limit;
      showAlert(err.msg, err.tone || 'warn', err.details);
      return;
    }

    startBtn.disabled = true;
    startBtn.textContent = 'Iniciando...';
    if (exportBox) exportBox.classList.add('hidden');
    hideAlert();
    allLeads = [];
    renderLeads();
    stopPoll();

    try {
      const emailGoal = clamp(emailGoalInput?.value, 1, 200, 20);
      const minFollowers = clamp(minFollowersInput?.value, 0, 999999, 0);

      const res = await fetch('/api/tiktok/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target,
          email_goal: emailGoal,
          min_followers: minFollowers,
        }),
      });

      if (!res.ok) {
        let msg = `Error ${res.status}`;
        let errorKey = 'job_failed';
        try {
          const err = await res.json();
          msg = err?.detail || msg;
          // Detectar errores específicos por mensaje
          if (msg.includes('rate') || msg.includes('limit')) {
            errorKey = res.status === 429 ? 'hourly_limit' : 'rate_limited';
          } else if (msg.includes('browser') || msg.includes('bot')) {
            errorKey = 'browser_blocked';
          }
        } catch (_) {}

        const errInfo = errorMessages[errorKey] || { msg, details: 'Intenta de nuevo o contacta soporte.' };
        showAlert(errInfo.msg, 'error', errInfo.details);
        throw new Error(msg);
      }

      const data = await res.json();
      currentJobId = data.job_id;
      if (exportBox) exportBox.classList.add('hidden');
      if (progressBox) progressBox.classList.remove('hidden');
      startPolling();
    } catch (err) {
      // Detectar si es error de red
      if (err.message === 'Failed to fetch' || err.message.includes('fetch')) {
        const errInfo = errorMessages.no_server;
        showAlert(errInfo.msg, 'error', errInfo.details);
      }
      // Si ya mostró un alert específico, no mostrar error genérico
      else if (!alertEl?.classList.contains('hidden') && alertEl?.textContent?.length > 0) {
        // Ya se mostró un error específico
      } else {
        showAlert('Error desconocido', 'error', 'Intenta de nuevo. Si persiste, reinicia: ./start_all.sh');
      }
    } finally {
      startBtn.disabled = false;
      startBtn.textContent = 'Iniciar extracción';
    }
  });

  // ── Export ────────────────────────────────────────────────────────────
  const exportLeads = () => {
    if (!allLeads.length) return;
    const cols = ['Usuario', 'Nickname', 'Email', 'Fuente', 'Seguidores', 'Verificado', 'Bio Link', 'Fecha'];
    const esc = (v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`;
    const rows = allLeads.map((l) => [
      l.username,
      l.nickname,
      l.email,
      l.email_source || '—',
      l.followers_count ?? 0,
      l.verified ? 'Sí' : 'No',
      l.bio_link || '—',
      formatDate(l.scraped_at),
    ].map(esc).join(','));
    const blob = new Blob(['\uFEFF' + [cols.join(','), ...rows].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tt_leads_${currentJobId || 'todos'}_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  exportBtn?.addEventListener('click', exportLeads);
  exportAllBtn?.addEventListener('click', exportLeads);

  // ── Leads table ───────────────────────────────────────────────────────
  const buildLeadRow = (lead) => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-slate-100 last:border-0 hover:bg-slate-50 transition';

    const cell = (cls, text, href = null) => {
      if (href) {
        const td = document.createElement('td');
        td.className = cls;
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.className = 'text-slate-800 hover:text-teal-700 hover:underline';
        a.textContent = text;
        td.appendChild(a);
        return td;
      }
      const td = document.createElement('td');
      td.className = cls;
      td.textContent = text;
      return td;
    };

    const username = safeText(lead.username);
    const nickname = safeText(lead.nickname);
    const email = safeText(lead.email);
    const followers = Number.isFinite(Number(lead.followers_count))
      ? Number(lead.followers_count).toLocaleString('es-ES')
      : '0';
    const verified = lead.verified ? '✓' : '—';
    const bioLink = safeText(lead.bio_link);

    const profileUrl = `https://www.tiktok.com/@${encodeURIComponent(username)}`;
    tr.appendChild(cell('px-4 py-3 font-medium text-slate-800 max-w-[140px] truncate', username, profileUrl));
    tr.appendChild(cell('px-4 py-3 text-slate-600 max-w-[160px] truncate', nickname));
    tr.appendChild(cell('px-4 py-3 text-slate-700 font-mono text-sm', email));
    tr.appendChild(cell('px-4 py-3 text-slate-500 text-xs hidden sm:table-cell', lead.email_source || '—'));
    tr.appendChild(cell('px-4 py-3 text-slate-600 text-right hidden md:table-cell', followers));
    tr.appendChild(cell('px-4 py-3 text-center hidden lg:table-cell', verified));

    const bioTd = document.createElement('td');
    bioTd.className = 'px-4 py-3 max-w-[140px] truncate hidden lg:table-cell';
    const safeWeb = toSafeHttpUrl(bioLink);
    if (safeWeb) {
      const a = document.createElement('a');
      a.href = safeWeb;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.className = 'text-blue-600 hover:underline text-xs';
      a.textContent = bioLink;
      bioTd.appendChild(a);
    } else {
      bioTd.textContent = '—';
    }
    tr.appendChild(bioTd);

    return tr;
  };

  const renderLeads = () => {
    if (!leadsTbody) return;
    leadsTbody.replaceChildren();
    if (allLeads.length) {
      for (const lead of allLeads) {
        leadsTbody.appendChild(buildLeadRow(lead));
      }
      if (leadsCount) leadsCount.textContent = `(${allLeads.length})`;
      if (exportAllBtn) exportAllBtn.classList.remove('hidden');
    } else {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-100 hover:bg-slate-50';
      const td = document.createElement('td');
      td.colSpan = 7;
      td.className = 'px-4 py-8 text-center text-slate-400 text-sm';
      td.textContent = 'Cargando leads...';
      tr.appendChild(td);
      leadsTbody.appendChild(tr);
      if (leadsCount) leadsCount.textContent = '';
      if (exportAllBtn) exportAllBtn.classList.add('hidden');
    }
  };

  const loadLeads = async () => {
    try {
      const url = currentJobId
        ? `/api/tiktok/leads?job_id=${encodeURIComponent(currentJobId)}`
        : '/api/tiktok/leads';
      const res = await fetch(url);
      if (!res.ok) throw new Error();
      allLeads = await res.json();
      renderLeads();
    } catch (_) {
      allLeads = [];
      renderLeads();
    }
  };

  // ── Jobs list ─────────────────────────────────────────────────────────
  const loadJobsList = async () => {
    if (!jobsList) return;
    try {
      const res = await fetch('/api/tiktok/jobs?limit=20');
      if (!res.ok) {
        jobsList.innerHTML = '<div class="px-5 py-6 text-center text-slate-400 text-sm">Error al cargar scrapeos</div>';
        return;
      }
      const jobs = await res.json();
      const arr = Array.isArray(jobs) ? jobs : [];

      jobsList.replaceChildren();
      if (!arr.length) {
        jobsList.innerHTML = '<div class="px-5 py-8 text-center text-slate-400 text-sm">Aún no hay scrapeos.</div>';
        jobsLoaded = true;
        return;
      }

      const statusBadgeClass = (s) => {
        if (s === 'failed') return 'bg-red-100 text-red-700';
        if (s === 'running') return 'bg-blue-100 text-blue-700';
        if (s === 'rate_limited') return 'bg-amber-100 text-amber-800';
        if (s === 'completed_partial') return 'bg-amber-100 text-amber-800';
        return 'bg-green-100 text-green-700';
      };

      const statusLabel = (s) => {
        if (s === 'failed') return 'Error';
        if (s === 'running') return 'En curso';
        if (s === 'rate_limited') return 'Pausado';
        if (s === 'completed_partial') return 'Parcial';
        return 'Completado';
      };

      for (const job of arr) {
        const jobId = job?.job_id;
        if (!jobId) continue;

        const card = document.createElement('a');
        card.href = '#';
        card.className = 'bg-white rounded-2xl border border-slate-200 px-4 py-3 flex items-center gap-4 hover:border-blue-300 hover:shadow-sm transition-all no-underline group';

        const iconWrap = document.createElement('div');
        iconWrap.className = 'w-10 h-10 rounded-lg bg-slate-900 flex items-center justify-center flex-shrink-0';
        iconWrap.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="white"><path d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-2.88 2.5 2.89 2.89 0 0 1-2.89-2.89 2.89 2.89 0 0 1 2.89-2.89c.28 0 .54.04.79.1V9.01a6.29 6.29 0 0 0-.79-.05 6.34 6.34 0 0 0-6.34 6.34 6.34 6.34 0 0 0 6.34 6.34 6.34 6.34 0 0 0 6.33-6.34V8.69a8.18 8.18 0 0 0 4.78 1.52V6.76a4.85 4.85 0 0 1-1.01-.07z"/></svg>';

        const content = document.createElement('div');
        content.className = 'flex-1 min-w-0';
        const target = document.createElement('div');
        target.className = 'font-medium text-slate-800 text-sm truncate';
        target.textContent = safeText(job?.target, '—');
        const detail = document.createElement('div');
        detail.className = 'text-slate-400 text-xs truncate';
        detail.textContent = formatDate(job?.started_at || job?.created_at);
        content.appendChild(target);
        content.appendChild(detail);

        const meta = document.createElement('div');
        meta.className = 'flex items-center gap-4 flex-shrink-0';
        const emailsDiv = document.createElement('div');
        emailsDiv.className = 'text-center hidden sm:block';
        emailsDiv.innerHTML = `<div class="text-sm font-semibold text-slate-700">${Number(job?.emails_found ?? 0)}</div><div class="text-[10px] text-slate-400">Emails</div>`;
        const statusBadge = document.createElement('span');
        statusBadge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${statusBadgeClass(job?.status)}`;
        statusBadge.textContent = statusLabel(job?.status);
        meta.appendChild(emailsDiv);
        meta.appendChild(statusBadge);

        card.appendChild(iconWrap);
        card.appendChild(content);
        card.appendChild(meta);

        card.addEventListener('click', async (e) => {
          e.preventDefault();
          currentJobId = String(jobId);
          setActiveSection('leads');
          await loadLeads();
        });

        jobsList.appendChild(card);
      }
      jobsLoaded = true;
    } catch (_) {
      jobsList.innerHTML = '<div class="px-5 py-6 text-center text-slate-400 text-sm">Error al cargar scrapeos</div>';
    }
  };

  refreshJobsBtn?.addEventListener('click', () => loadJobsList().catch(() => {}));

  // ── Initial boot ──────────────────────────────────────────────────────
  const initialHealth = page.dataset.health ? JSON.parse(page.dataset.health) : {};
  const initialJobs = page.dataset.recentJobs ? JSON.parse(page.dataset.recentJobs) : [];

  setActiveSection(activeSection);
  updateLimitsUi(initialHealth);
  hideAlert();

  // Load state
  (async () => {
    await Promise.allSettled([
      loadLimits(),
    ]);
  })();

  // Initial leads
  if (activeSection === 'leads') {
    loadLeads().catch(() => {});
  } else if (activeSection === 'scrapeos') {
    loadJobsList().catch(() => {});
    if (currentJobId) loadLeads().catch(() => {});
  }

  // Attach to running job if any
  const runningJob = initialJobs.find((j) => j.status === 'running');
  if (runningJob?.job_id && !currentJobId) {
    currentJobId = runningJob.job_id;
    setActiveSection('extraccion');
    if (progressBox) progressBox.classList.remove('hidden');
    startPolling();
  }

  // Refresh limits every 30s
  window.setInterval(loadLimits, 30000);

  // Refresh jobs list every 20s if in scrapeos section
  window.setInterval(() => {
    if (activeSection === 'scrapeos') {
      loadJobsList().catch(() => {});
    }
  }, 20000);
}
