import {
  startProxyStatusPolling,
  subscribeProxyStatus,
} from '../lib/proxy-state.js';

function getStatusColorClass(availableNow, dailyPct) {
  if (availableNow <= 2 || dailyPct >= 80) return 'bg-red-500';
  if (availableNow <= 5 || dailyPct >= 50) return 'bg-yellow-400';
  return 'bg-green-400';
}

function renderSearchProxyWidget(proxyStatus, isScrapingInProgress) {
  const widget = document.getElementById('proxy-widget');
  if (!widget) return;

  if (!proxyStatus || proxyStatus.total_proxies === 0) {
    widget.classList.add('hidden');
    return;
  }

  widget.classList.remove('hidden');

  const dot = document.getElementById('proxy-dot');
  const label = document.getElementById('proxy-label');
  const reqLabel = document.getElementById('proxy-requests-label');
  const barFill = document.getElementById('proxy-bar-fill');
  const pctLabel = document.getElementById('proxy-pct-label');

  const available = proxyStatus.available_now;
  const total = proxyStatus.total_proxies;
  const dailyPct = proxyStatus.daily_requests_limit > 0
    ? Math.round((proxyStatus.daily_requests_used / proxyStatus.daily_requests_limit) * 100)
    : 0;
  const colorClass = getStatusColorClass(available, dailyPct);

  if (dot) {
    dot.className = `w-2.5 h-2.5 rounded-full flex-shrink-0 ${colorClass}`;
  }

  if (barFill) {
    barFill.className = `h-1.5 rounded-full transition-all ${colorClass}`;
    barFill.style.width = `${dailyPct}%`;
  }

  if (label) {
    label.textContent = `Proxies: ${available}/${total} disponibles`;
  }

  if (reqLabel) {
    reqLabel.textContent = `Requests hoy: ${proxyStatus.daily_requests_used.toLocaleString()} / ${proxyStatus.daily_requests_limit.toLocaleString()}`;
  }

  if (pctLabel) {
    pctLabel.textContent = `${dailyPct}%`;
  }

  const dailyExhausted = proxyStatus.daily_requests_remaining === 0 && proxyStatus.total_proxies > 0;
  const startBtn = document.getElementById('start-btn');
  if (!startBtn || isScrapingInProgress()) return;

  startBtn.disabled = dailyExhausted;
  startBtn.title = dailyExhausted ? 'Límite diario de requests agotado. Se reiniciará mañana.' : '';
}

function renderSidebarProxyWidget(proxyStatus) {
  const widget = document.getElementById('sidebar-proxy');
  if (!widget) return;

  // `subscribeProxyStatus()` llama inmediatamente con `null` al inicio
  // para indicar "estado desconocido". No queremos pisar el render inicial
  // del servidor con valores vacíos.
  if (proxyStatus === null || proxyStatus === undefined) {
    return;
  }

  // Preserve the last valid status to avoid flicker when polling returns
  // transient empty/0 values during upstream timeouts or cold starts.
  renderSidebarProxyWidget._lastValid = renderSidebarProxyWidget._lastValid || null;
  const safe = proxyStatus || {};

  const total = Number(safe.total_proxies ?? 0);
  const available = Number(safe.available_now ?? 0);
  const used = Number(safe.daily_requests_used ?? 0);
  const limit = Number(safe.daily_requests_limit ?? 0);

  // Keep a richer lastValid: sometimes total_proxies may briefly be 0/undefined
  // while we still get meaningful available_now and usage counters.
  const shouldStore =
    (Number.isFinite(total) && total > 0) ||
    (Number.isFinite(available) && available > 0) ||
    (Number.isFinite(limit) && limit > 0) ||
    (Number.isFinite(used) && used > 0);

  if (shouldStore) {
    renderSidebarProxyWidget._lastValid = safe;
  }

  const last = renderSidebarProxyWidget._lastValid || null;
  const display =
    (Number.isFinite(total) && total > 0) || last === null
      ? safe
      : last;

  const dot = document.getElementById('sp-dot');
  const label = document.getElementById('sp-label');
  const bar = document.getElementById('sp-bar');

  widget.classList.remove('hidden');

  const displayTotal = Number(display.total_proxies ?? 0);
  const displayAvailable = Number(display.available_now ?? 0);
  const displayUsed = Number(display.daily_requests_used ?? 0);
  const displayLimit = Number(display.daily_requests_limit ?? 1);
  const dailyPct = displayLimit > 0 ? Math.round((displayUsed / displayLimit) * 100) : 0;
  const colorClass = getStatusColorClass(displayAvailable, dailyPct);

  if (dot) {
    dot.className = `w-2 h-2 rounded-full flex-shrink-0 ${colorClass}`;
  }

  if (label) {
    if (displayTotal > 0) {
      label.textContent = `${displayAvailable}/${displayTotal} disponibles`;
    } else if (displayAvailable > 0) {
      label.textContent = `${displayAvailable} disponibles`;
    } else {
      label.textContent = '— disponibles';
    }
  }

  if (bar) {
    bar.className = `h-1.5 rounded-full transition-all ${colorClass}`;
    // Keep bar stable (based on usage counters) even if total_proxies is 0.
    bar.style.width = Number.isFinite(dailyPct) ? `${dailyPct}%` : '0%';
  }
}

export function initProxyStatus({ isScrapingInProgress = () => false } = {}) {
  subscribeProxyStatus((proxyStatus) => {
    renderSearchProxyWidget(proxyStatus, isScrapingInProgress);
    renderSidebarProxyWidget(proxyStatus);
  });

  startProxyStatusPolling({ intervalMs: 30000, immediate: true });
}
