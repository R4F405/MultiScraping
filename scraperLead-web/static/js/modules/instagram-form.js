import { emailStatusClass, safeText, toSafeHttpUrl } from '../lib/dom-utils.js';

export function initInstagramForm() {
  const page = document.getElementById('instagram-form-page');
  if (!page) return;

  // ── State ─────────────────────────────────────────────────────────────
  let activeMode = 'dorking'; // 'dorking' | 'followers'
  let dorkingJobId = null;
  let followersJobId = null;
  let dorkingPollInterval = null;
  let followersPollInterval = null;
  let allLeads = [];
  let filteredLeads = [];
  let activeFilters = { hasEmail: false, businessOnly: false };
  let profileVerified = false;
  let igView = 'todos';
  let displayedJobId = null;
  let jobsLoaded = false;
  let sessionActive = false;
  let limitsState = {
    can_start_dorking: true,
    can_start_followers: true,
    unauth_daily_reached: false,
    auth_daily_reached: false,
    auth_hourly_reached: false,
  };

  // Hard limits — never configurable from UI to avoid accidental bans
  const MAX_UNAUTH_DAILY = 150;
  const MAX_AUTH_DAILY = 150;

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

  // Status row
  const healthDot = document.getElementById('ig-health-dot');
  const healthText = document.getElementById('ig-health-text');
  const healthDetails = document.getElementById('ig-health-details');
  const sessionDot = document.getElementById('ig-session-dot');
  const sessionText = document.getElementById('ig-session-text');
  const sessionDetails = document.getElementById('ig-session-details');
  const usageUnauth = document.getElementById('ig-usage-unauth');
  const usageAuth = document.getElementById('ig-usage-auth');
  const usageHourly = document.getElementById('ig-usage-hourly');

  // Tabs
  const tabDorking = document.getElementById('ig-tab-dorking');
  const tabFollowers = document.getElementById('ig-tab-followers');
  const panelDorking = document.getElementById('ig-panel-dorking');
  const panelFollowers = document.getElementById('ig-panel-followers');

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

  // Mode B — followers
  const loginPanel = document.getElementById('ig-login-panel');
  const followersPanel = document.getElementById('ig-followers-panel');
  const loginUser = document.getElementById('ig-login-user');
  const loginPass = document.getElementById('ig-login-pass');
  const loginBtn = document.getElementById('ig-login-btn');
  const loginAlert = document.getElementById('ig-login-alert');
  const logoutBtn = document.getElementById('ig-logout-btn');
  const targetInput = document.getElementById('ig-target');
  const followersEmailGoal = document.getElementById('ig-followers-email-goal');
  const checkProfileBtn = document.getElementById('ig-check-profile-btn');
  const followersStartBtn = document.getElementById('ig-followers-start-btn');
  const followersExportBtn = document.getElementById('ig-followers-export-btn');
  const followersProgress = document.getElementById('ig-followers-progress');
  const followersBar = document.getElementById('ig-followers-bar');
  const followersProgressText = document.getElementById('ig-followers-progress-text');
  const followersEmailsText = document.getElementById('ig-followers-emails-text');
  const profilePreview = document.getElementById('ig-profile-preview');
  const profileAvatar = document.getElementById('ig-profile-avatar');
  const profileUsername = document.getElementById('ig-profile-username');
  const profileMeta = document.getElementById('ig-profile-meta');

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
  const hideLimitAlert = () => limitAlertEl?.classList.add('hidden');

  const showLimitAlert = (msg, tone = 'warn') => {
    if (!limitAlertEl) return;
    const cls = tone === 'error'
      ? 'bg-red-50 border-red-200 text-red-700'
      : 'bg-amber-50 border-amber-200 text-amber-800';
    limitAlertEl.className = `mb-4 rounded-xl border px-4 py-3 text-sm ${cls}`;
    limitAlertEl.textContent = msg;
    limitAlertEl.classList.remove('hidden');
  };

  const showLoginAlert = (msg, tone = 'error') => {
    const cls = {
      error: 'bg-red-50 border-red-200 text-red-700',
      warn: 'bg-amber-100 border-amber-300 text-amber-800',
      ok: 'bg-green-50 border-green-200 text-green-700',
    };
    loginAlert.className = `mb-3 text-xs px-3 py-2 rounded-lg border ${cls[tone] || cls.error}`;
    loginAlert.textContent = msg;
    loginAlert.classList.remove('hidden');
  };
  const hideLoginAlert = () => loginAlert?.classList.add('hidden');

  const formatDate = (value) => {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString('es-ES');
  };

  const clamp = (val, min, max, fallback) => {
    const n = parseInt(String(val || ''), 10);
    return Number.isNaN(n) ? fallback : Math.max(min, Math.min(max, n));
  };

  // ── Tabs ──────────────────────────────────────────────────────────────
  const setActiveTab = (mode) => {
    activeMode = mode;
    const isDorking = mode === 'dorking';

    tabDorking.className = `px-4 py-2 rounded-lg text-sm font-medium transition ${
      isDorking ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
    }`;
    tabFollowers.className = `px-4 py-2 rounded-lg text-sm font-medium transition ${
      !isDorking ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700'
    }`;

    panelDorking.classList.toggle('hidden', !isDorking);
    panelFollowers.classList.toggle('hidden', isDorking);
  };

  tabDorking.addEventListener('click', () => setActiveTab('dorking'));
  tabFollowers.addEventListener('click', () => setActiveTab('followers'));

  // ── Health ────────────────────────────────────────────────────────────
  const updateHealthUi = (health) => {
    const status = health?.status || 'unknown';
    const map = {
      ok: { dot: 'bg-green-500', text: 'Scraper operativo', details: health?.message || 'Health check OK' },
      broken: { dot: 'bg-red-500', text: 'Scraper con errores', details: health?.message || 'Revisar configuración' },
      unknown: { dot: 'bg-slate-300', text: 'Estado desconocido', details: 'No se pudo consultar el health.' },
    };
    const ui = map[status] || map.unknown;
    if (healthDot) healthDot.className = `w-2.5 h-2.5 rounded-full ${ui.dot} shrink-0`;
    if (healthText) healthText.textContent = ui.text;
    if (healthDetails) healthDetails.textContent = ui.details;
  };

  // ── Session ───────────────────────────────────────────────────────────
  const updateSessionUi = (session) => {
    sessionActive = Boolean(session?.logged_in);

    if (sessionActive) {
      const user = session.username ? `@${session.username}` : 'activa';
      const age = session.session_age_hours != null ? ` (hace ${session.session_age_hours}h)` : '';
      if (sessionDot) sessionDot.className = 'w-2.5 h-2.5 rounded-full bg-purple-500 shrink-0';
      if (sessionText) sessionText.textContent = `Sesión activa`;
      if (sessionDetails) sessionDetails.textContent = `${user}${age}`;

      loginPanel?.classList.add('hidden');
      followersPanel?.classList.remove('hidden');
    } else {
      if (sessionDot) sessionDot.className = 'w-2.5 h-2.5 rounded-full bg-slate-300 shrink-0';
      if (sessionText) sessionText.textContent = 'Sin sesión';
      if (sessionDetails) sessionDetails.textContent = 'Necesaria para Modo B';

      loginPanel?.classList.remove('hidden');
      followersPanel?.classList.add('hidden');
    }

    updateActionButtons();
  };

  const loadSession = async () => {
    try {
      const res = await fetch('/api/instagram/session');
      if (!res.ok) { updateSessionUi(null); return; }
      const data = await res.json();
      updateSessionUi(data);
    } catch (_) { updateSessionUi(null); }
  };

  const updateActionButtons = () => {
    if (dorkingStartBtn) dorkingStartBtn.disabled = !limitsState.can_start_dorking;
    if (!followersStartBtn) return;
    followersStartBtn.disabled = !sessionActive || !profileVerified || !limitsState.can_start_followers;
  };

  // ── Usage stats (read-only, no configuration) ────────────────────────
  const loadUsage = async () => {
    try {
      const res = await fetch('/api/instagram/limits');
      if (!res.ok) return;
      const data = await res.json();
      if (usageUnauth) usageUnauth.textContent = `${data.used_today_unauth ?? '—'}/${MAX_UNAUTH_DAILY}`;
      if (usageAuth) usageAuth.textContent = `${data.used_today_auth ?? '—'}/${MAX_AUTH_DAILY}`;
      if (usageHourly) usageHourly.textContent = `Esta hora: ${data.used_this_hour_auth ?? '—'}/${data.hourly_auth ?? 35}`;

      limitsState = {
        can_start_dorking: Boolean(data.can_start_dorking),
        can_start_followers: Boolean(data.can_start_followers),
        unauth_daily_reached: Boolean(data.unauth_daily_reached),
        auth_daily_reached: Boolean(data.auth_daily_reached),
        auth_hourly_reached: Boolean(data.auth_hourly_reached),
      };
      updateActionButtons();

      const blocks = [];
      if (limitsState.unauth_daily_reached) blocks.push('Modo A bloqueado por límite diario');
      if (limitsState.auth_hourly_reached) blocks.push('Modo B bloqueado por límite por hora');
      if (limitsState.auth_daily_reached) blocks.push('Modo B bloqueado por límite diario');
      if (blocks.length) {
        showLimitAlert(`${blocks.join(' · ')}. Debes esperar antes de iniciar otro scrapeo.`);
      } else {
        hideLimitAlert();
      }
    } catch (_) {}
  };

  // ── Login / Logout ────────────────────────────────────────────────────
  loginBtn?.addEventListener('click', async () => {
    hideLoginAlert();
    const username = loginUser?.value.trim();
    const password = loginPass?.value;
    if (!username || !password) {
      showLoginAlert('Introduce usuario y contraseña.', 'warn');
      return;
    }
    loginBtn.disabled = true;
    loginBtn.textContent = 'Iniciando sesión...';
    try {
      const res = await fetch('/api/instagram/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (data.status === 'ok') {
        loginPass.value = '';
        await loadSession();
        await loadPoolAccounts();
      } else if (data.status === '2fa' || data.status === '2fa_required') {
        showLoginAlert(data.message || 'Se requiere verificación en dos pasos (2FA).', 'warn');
      } else if (data.status === 'challenge' || data.status === 'phone') {
        showLoginAlert(data.message || 'Instagram requiere verificación manual.', 'warn');
      } else if (data.status === 'ip_blocked' || data.status === 'account_issue' || data.status === 'network') {
        // Errores que no se pueden solucionar con "Reconectar" — mostrar como error
        showLoginAlert(data.message || 'No se pudo iniciar sesión. Revisa el mensaje arriba.', 'error');
      } else {
        showLoginAlert(data.message || 'Login fallido. Verifica las credenciales.', 'error');
      }
    } catch (_) {
      showLoginAlert('No se pudo conectar con InstaLeads. ¿Está el servidor activo?', 'error');
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = 'Iniciar sesión';
    }
  });

  logoutBtn?.addEventListener('click', async () => {
    try {
      await fetch('/api/instagram/session', { method: 'DELETE' });
    } catch (_) {}
    updateSessionUi(null);
  });

  // ── Profile preview (Mode B) ──────────────────────────────────────────
  const hideProfilePreview = () => {
    profilePreview?.classList.add('hidden');
    if (profileAvatar) profileAvatar.src = '';
    if (profileUsername) profileUsername.textContent = '';
    if (profileMeta) profileMeta.textContent = '';
    profileVerified = false;
  };

  const showProfilePreview = (profile) => {
    const uname = safeText(profile?.username, '').trim();
    if (!uname) { hideProfilePreview(); return; }
    profilePreview?.classList.remove('hidden');
    if (profileUsername) profileUsername.textContent = `@${uname}`;
    const followers = Number.isFinite(Number(profile?.follower_count))
      ? Number(profile.follower_count).toLocaleString('es-ES') : '0';
    const biz = profile?.is_business_account ? 'Business' : 'Personal';
    const priv = profile?.is_private ? 'Privada' : 'Pública';
    if (profileMeta) profileMeta.textContent = `${followers} seguidores · ${biz} · ${priv}`;
    if (profileAvatar) {
      const rawAvatarUrl = String(profile?.profile_pic_url || '').trim();
      profileAvatar.src = rawAvatarUrl
        ? `/api/instagram/avatar?url=${encodeURIComponent(rawAvatarUrl)}`
        : 'about:blank';
    }
  };

  const updateFollowersStartBtn = () => updateActionButtons();

  checkProfileBtn?.addEventListener('click', async () => {
    hideAlert();
    const target = String(targetInput?.value || '').trim().replace(/^@/, '');
    if (!target) { showAlert('Introduce el username a comprobar.'); return; }

    checkProfileBtn.disabled = true;
    checkProfileBtn.textContent = 'Comprobando...';
    hideProfilePreview();

    try {
      const res = await fetch(`/api/instagram/profile/${encodeURIComponent(target)}`);
      if (!res.ok) {
        profileVerified = false;
        showAlert(res.status === 404
          ? 'Perfil no encontrado o privado.'
          : 'No se pudo comprobar el perfil ahora mismo.', 'warn');
      } else {
        const profile = await res.json();
        showProfilePreview(profile);
        profileVerified = true;
        hideAlert();
      }
    } catch (_) {
      profileVerified = false;
      showAlert('Error de conexión al comprobar el perfil.');
    } finally {
      checkProfileBtn.disabled = false;
      checkProfileBtn.textContent = 'Comprobar perfil';
      updateFollowersStartBtn();
    }
  });

  targetInput?.addEventListener('input', () => {
    profileVerified = false;
    hideProfilePreview();
    updateFollowersStartBtn();
  });

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
    if (progressTextEl) progressTextEl.textContent = `Progreso: ${progress}/${total || '?'} (${pct}%)`;
    if (emailsTextEl) {
      emailsTextEl.textContent = `${emails} emails encontrados · IG: ${fromIg} · Web: ${fromWeb} · Enrichment: ${enrichSuccesses}/${enrichAttempts}`;
    }
  };

  const hideProgress = (wrapEl, barEl) => {
    wrapEl?.classList.add('hidden');
    if (barEl) barEl.style.width = '0%';
  };

  // ── Polling ───────────────────────────────────────────────────────────
  const stopPoll = (intervalRef) => { if (intervalRef) clearInterval(intervalRef); };

  const makePollFn = (jobIdGetter, barEl, progressTextEl, emailsTextEl, progressWrapEl, exportBtnEl, modeLabel) => {
    return async () => {
      const jobId = jobIdGetter();
      if (!jobId) return;
      try {
        const res = await fetch(`/api/instagram/jobs/${encodeURIComponent(jobId)}`);
        if (!res.ok) return;
        const job = await res.json();
        updateProgress(job, barEl, progressTextEl, emailsTextEl, progressWrapEl);

        if (job.status === 'completed') {
          if (modeLabel === 'dorking') { stopPoll(dorkingPollInterval); dorkingPollInterval = null; }
          else { stopPoll(followersPollInterval); followersPollInterval = null; }
          if (exportBtnEl) exportBtnEl.disabled = false;
          await loadResults(igView === 'todos' ? null : (displayedJobId || jobId));
          // Refresh the jobs list so the completed job appears in Scrapeos section
          if (igView === 'scrapeos') {
            await renderJobsList();
          }
          showAlert('Extracción completada.', 'ok');
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
          if (modeLabel === 'dorking') { stopPoll(dorkingPollInterval); dorkingPollInterval = null; }
          else { stopPoll(followersPollInterval); followersPollInterval = null; }
          const emails = Math.max(0, Number(job?.emails_found ?? 0));
          const progress = Math.max(0, Number(job?.progress ?? 0));
          const total = Math.max(0, Number(job?.total ?? 0));
          const reason = safeText(job?.status_detail) || (modeLabel === 'followers'
            ? 'límite horario o diario del modo con sesión'
            : 'límite diario del modo sin sesión');
          showAlert(
            `Extracción detenida por ${reason}. Se han guardado ${emails} emails (${progress}/${total || '?'} perfiles procesados).`,
            'warn',
          );
          await loadUsage();
        } else if (job.status === 'failed') {
          if (modeLabel === 'dorking') { stopPoll(dorkingPollInterval); dorkingPollInterval = null; }
          else { stopPoll(followersPollInterval); followersPollInterval = null; }
          showAlert(`Extracción fallida: ${safeText(job?.failure_reason) || 'revisa los logs.'}`, 'error');
        }
      } catch (_) {}
    };
  };

  // ── Mode A — dorking ──────────────────────────────────────────────────
  dorkingStartBtn?.addEventListener('click', async () => {
    hideAlert();
    const niche = nicheInput?.value.trim();
    const location = locationInput?.value.trim();
    if (!niche) { showAlert('Introduce el nicho (ej: fotógrafo).'); return; }
    if (!location) { showAlert('Introduce la ubicación (ej: Valencia).'); return; }

    if (!limitsState.can_start_dorking) {
      showAlert('No puedes iniciar Modo A: se alcanzó el límite diario.', 'warn');
      return;
    }

    dorkingStartBtn.disabled = true;
    dorkingStartBtn.textContent = 'Buscando...';
    if (dorkingExportBtn) dorkingExportBtn.disabled = true;
    hideProgress(dorkingProgress, dorkingBar);
    allLeads = []; applyFilters();
    if (dorkingPollInterval) { clearInterval(dorkingPollInterval); dorkingPollInterval = null; }

    try {
      const emailGoal = clamp(dorkingEmailGoal?.value, 1, 500, 20);
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
        makePollFn(() => dorkingJobId, dorkingBar, dorkingProgressText, dorkingEmailsText, dorkingProgress, dorkingExportBtn, 'dorking'),
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

  // ── Mode B — followers ────────────────────────────────────────────────
  followersStartBtn?.addEventListener('click', async () => {
    hideAlert();
    const target = String(targetInput?.value || '').trim().replace(/^@/, '');
    if (!target) { showAlert('Introduce el username objetivo.'); return; }
    if (!profileVerified) { showAlert('Primero pulsa "Comprobar perfil".', 'warn'); return; }
    if (!sessionActive) { showAlert('Necesitas sesión activa para Modo B.', 'warn'); return; }
    if (!limitsState.can_start_followers) {
      showAlert('No puedes iniciar Modo B: se alcanzó un límite de uso (hora o día).', 'warn');
      return;
    }

    followersStartBtn.disabled = true;
    followersStartBtn.textContent = 'Iniciando...';
    if (followersExportBtn) followersExportBtn.disabled = true;
    hideProgress(followersProgress, followersBar);
    allLeads = []; applyFilters();
    if (followersPollInterval) { clearInterval(followersPollInterval); followersPollInterval = null; }

    try {
      const emailGoal = clamp(followersEmailGoal?.value, 1, 500, 20);
      const res = await fetch('/api/instagram/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'followers', target, email_goal: emailGoal }),
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
      followersJobId = data.job_id;
      updateProgress({ progress: 0, total: emailGoal, emails_found: 0 }, followersBar, followersProgressText, followersEmailsText, followersProgress);
      followersPollInterval = window.setInterval(
        makePollFn(() => followersJobId, followersBar, followersProgressText, followersEmailsText, followersProgress, followersExportBtn, 'followers'),
        2000,
      );
    } catch (err) {
      showAlert(`No se pudo iniciar la extracción: ${err.message}`);
    } finally {
      followersStartBtn.textContent = 'Extraer seguidores';
      updateFollowersStartBtn();
    }
  });

  followersExportBtn?.addEventListener('click', () => exportLeads(followersJobId));

  // ── Export ────────────────────────────────────────────────────────────
  const exportLeads = (jobId) => {
    if (!filteredLeads.length) return;
    const cols = ['Username','Nombre','Email','Estado email','Fuente email','Followers','Tipo cuenta','Origen','Web'];
    const esc = (v) => `"${(v ?? '').toString().replace(/"/g, '""')}"`;
    const rows = filteredLeads.map((l) => [
      l.username, l.full_name, l.email, l.email_status, l.email_source,
      l.followers_count, Boolean(l.is_business) ? 'Business' : 'Personal',
      l.source_type, l.website,
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
    tr.appendChild(usernameTd);
    tr.appendChild(cell('px-4 py-3 text-slate-600 max-w-[200px] truncate', fullName, fullName));
    tr.appendChild(cell(`px-4 py-3 ${emailStatusClass(lead.email_status)}`, email));
    tr.appendChild(cell('px-4 py-3 text-slate-500 text-xs', safeText(lead.email_source)));
    tr.appendChild(cell('px-4 py-3 text-slate-600', followers));

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

    // Source type badge
    const srcTd = document.createElement('td');
    srcTd.className = 'px-4 py-3';
    const srcBadge = document.createElement('span');
    const src = safeText(lead.source_type);
    srcBadge.className = src === 'dorking'
      ? 'inline-flex items-center text-xs font-medium bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full'
      : 'inline-flex items-center text-xs font-medium bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full';
    srcBadge.textContent = src === 'dorking' ? 'Dorking' : src === 'followers' ? 'Followers' : src || '—';
    srcTd.appendChild(srcBadge);
    tr.appendChild(srcTd);

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
        return 'bg-green-100 text-green-700';
      };
      const statusLabel = (s) => {
        if (s === 'failed') return 'Error';
        if (s === 'running') return 'En curso';
        if (s === 'waiting_rate_window') return 'Pausado';
        if (s === 'rate_limited') return 'Límite';
        return 'Completado';
      };
      const modeColor = (m) => m === 'dorking' ? 'bg-orange-100 text-orange-600' : 'bg-purple-100 text-purple-600';
      const modeLabel = (m) => m === 'dorking' ? 'Dorking' : 'Followers';

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
        totalEl.innerHTML = `<div class="text-base font-semibold text-slate-800">${Number(job?.total ?? 0)}</div><div class="text-[10px] text-slate-400">Perfiles</div>`;
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
  setActiveTab('dorking');
  setIgViewUi(igView);

  // Load state in parallel
  (async () => {
    await Promise.allSettled([
      loadSession(),
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
      setActiveTab('dorking');
      dorkingProgress?.classList.remove('hidden');
      dorkingPollInterval = window.setInterval(
        makePollFn(() => dorkingJobId, dorkingBar, dorkingProgressText, dorkingEmailsText, dorkingProgress, dorkingExportBtn, 'dorking'),
        2000,
      );
    } else if (mode === 'followers') {
      followersJobId = runningJob.job_id;
      setActiveTab('followers');
      followersProgress?.classList.remove('hidden');
      followersPollInterval = window.setInterval(
        makePollFn(() => followersJobId, followersBar, followersProgressText, followersEmailsText, followersProgress, followersExportBtn, 'followers'),
        2000,
      );
    }
  }

  // Refresh limits every 30s
  window.setInterval(loadUsage, 30000);

  // ── Account pool management ────────────────────────────────────────────────

  const poolToggleBtn = document.getElementById('ig-pool-toggle-btn');
  const poolAddForm = document.getElementById('ig-pool-add-form');
  const poolAddBtn = document.getElementById('ig-pool-add-btn');
  const poolCancelBtn = document.getElementById('ig-pool-cancel-btn');
  const poolUsernameInput = document.getElementById('ig-pool-username');
  const poolPasswordInput = document.getElementById('ig-pool-password');
  const poolProxyInput = document.getElementById('ig-pool-proxy');
  const poolAddStatus = document.getElementById('ig-pool-add-status');
  const poolTable = document.getElementById('ig-pool-table');
  const poolTbody = document.getElementById('ig-pool-tbody');
  const poolEmpty = document.getElementById('ig-pool-empty');

  // Banner de alerta para cuentas que necesitan login manual
  const poolAlertBanner = document.getElementById('ig-pool-alert-banner');

  function renderPoolAccounts(accounts) {
    if (!poolTbody) return;
    poolTbody.innerHTML = '';

    // Show/hide banner if any account needs manual login
    const manualAccounts = (accounts || []).filter((a) => a.needs_manual_login);
    if (poolAlertBanner) {
      if (manualAccounts.length > 0) {
        const names = manualAccounts.map((a) => `@${a.username}`).join(', ');
        poolAlertBanner.textContent = `⚠ Login manual requerido en: ${names}. Abre la app de Instagram, completa la verificación y pulsa "Reconectar".`;
        poolAlertBanner.classList.remove('hidden');
      } else {
        poolAlertBanner.classList.add('hidden');
      }
    }

    if (!accounts || accounts.length === 0) {
      poolTable?.classList.add('hidden');
      poolEmpty?.classList.remove('hidden');
      return;
    }
    poolTable?.classList.remove('hidden');
    poolEmpty?.classList.add('hidden');

    for (const acc of accounts) {
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-50 hover:bg-slate-50 transition';

      const needsManual = Boolean(acc.needs_manual_login);
      const statusColor = needsManual
        ? 'text-amber-600'
        : acc.status === 'active' ? 'text-green-600'
        : acc.status === 'cooldown' ? 'text-amber-600'
        : 'text-slate-400';
      const statusLabel = needsManual ? 'Login manual' : acc.status === 'active' ? 'Activa' : acc.status === 'cooldown' ? 'Cooldown' : 'Desactivada';
      const primaryBadge = acc.is_primary
        ? `<span class="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-violet-100 text-violet-600 font-semibold">Principal</span>`
        : '';
      const reloginBtn = needsManual
        ? `<button class="ig-pool-relogin-btn text-xs text-amber-600 hover:text-amber-800 border border-amber-200 hover:border-amber-400 px-2 py-1 rounded-lg transition mr-2" data-username="${acc.username}" title="${acc.manual_login_reason || ''}">Reconectar</button>`
        : '';

      tr.innerHTML = `
        <td class="px-3 py-2 font-medium text-slate-800">@${acc.username}${primaryBadge}</td>
        <td class="px-3 py-2 text-xs font-semibold ${statusColor}">${statusLabel}</td>
        <td class="px-3 py-2 text-slate-500">${acc.requests_this_hour ?? 0}/35</td>
        <td class="px-3 py-2 text-slate-400 text-xs truncate max-w-[140px]">${acc.proxy_url || '—'}</td>
        <td class="px-3 py-2 text-right whitespace-nowrap">
          ${reloginBtn}
          <button class="ig-pool-remove-btn text-xs text-red-500 hover:text-red-700 transition" data-username="${acc.username}">Eliminar</button>
        </td>
      `;
      poolTbody.appendChild(tr);
    }

    poolTbody.querySelectorAll('.ig-pool-remove-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const username = btn.dataset.username;
        if (!confirm(`¿Eliminar la cuenta @${username} del pool?`)) return;
        try {
          await fetch(`/api/instagram/accounts/${encodeURIComponent(username)}`, { method: 'DELETE' });
          await loadPoolAccounts();
          await loadSession();
        } catch (err) {
          console.error('Error removing account:', err);
        }
      });
    });

    poolTbody.querySelectorAll('.ig-pool-relogin-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const username = btn.dataset.username;
        btn.disabled = true;
        btn.textContent = 'Reconectando…';
        try {
          const res = await fetch(`/api/instagram/accounts/relogin/${encodeURIComponent(username)}`, {
            method: 'POST',
          });
          const data = await res.json();
          if (data.status === 'ok') {
            await loadPoolAccounts();
            await loadSession();
          } else if (data.status === 'challenge' || data.status === 'phone' || data.status === '2fa') {
            btn.disabled = false;
            btn.textContent = 'Reconectar';
            if (poolAlertBanner) {
              poolAlertBanner.textContent = `⚠ @${username} sigue requiriendo verificación manual: ${data.message}`;
              poolAlertBanner.classList.remove('hidden');
            }
          } else {
            btn.disabled = false;
            btn.textContent = 'Reconectar';
          }
        } catch (_) {
          btn.disabled = false;
          btn.textContent = 'Reconectar';
        }
      });
    });
  }

  async function loadPoolAccounts() {
    try {
      const res = await fetch('/api/instagram/accounts');
      if (!res.ok) return;
      const accounts = await res.json();
      renderPoolAccounts(accounts);
    } catch (_) {}
  }

  poolToggleBtn?.addEventListener('click', () => {
    const hidden = poolAddForm?.classList.contains('hidden');
    if (hidden) {
      poolAddForm?.classList.remove('hidden');
      poolToggleBtn.textContent = 'Cancelar';
    } else {
      poolAddForm?.classList.add('hidden');
      poolToggleBtn.textContent = 'Añadir cuenta';
    }
  });

  poolCancelBtn?.addEventListener('click', () => {
    poolAddForm?.classList.add('hidden');
    poolToggleBtn.textContent = 'Añadir cuenta';
    if (poolAddStatus) poolAddStatus.textContent = '';
  });

  poolAddBtn?.addEventListener('click', async () => {
    const username = poolUsernameInput?.value?.trim();
    const password = poolPasswordInput?.value;
    const proxyUrl = poolProxyInput?.value?.trim() || null;

    if (!username || !password) {
      if (poolAddStatus) poolAddStatus.textContent = 'Usuario y contraseña requeridos.';
      return;
    }

    poolAddBtn.disabled = true;
    if (poolAddStatus) poolAddStatus.textContent = 'Iniciando sesión…';

    try {
      const res = await fetch('/api/instagram/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, proxy_url: proxyUrl }),
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = data.message || data.detail || 'Error al añadir la cuenta.';
        if (poolAddStatus) poolAddStatus.textContent = msg;
        return;
      }
      if (poolAddStatus) poolAddStatus.textContent = `✓ Cuenta @${username} añadida.`;
      if (poolUsernameInput) poolUsernameInput.value = '';
      if (poolPasswordInput) poolPasswordInput.value = '';
      if (poolProxyInput) poolProxyInput.value = '';
      await loadPoolAccounts();
      await loadSession();
      setTimeout(() => {
        poolAddForm?.classList.add('hidden');
        poolToggleBtn.textContent = 'Añadir cuenta';
        if (poolAddStatus) poolAddStatus.textContent = '';
      }, 1500);
    } catch (err) {
      if (poolAddStatus) poolAddStatus.textContent = 'Error de conexión.';
    } finally {
      poolAddBtn.disabled = false;
    }
  });

  // Load pool accounts on init and refresh every 30s
  loadPoolAccounts().catch(() => {});
  window.setInterval(loadPoolAccounts, 30000);

  // Refresh jobs list periodically so new completed jobs appear in Scrapeos history
  window.setInterval(() => {
    if (jobsGrid && igView === 'scrapeos') {
      renderJobsList().catch(() => {});
    }
  }, 15000);
}
