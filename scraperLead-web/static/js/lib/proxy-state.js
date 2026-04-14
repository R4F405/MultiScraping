let currentProxyStatus = null;
let pollIntervalId = null;
const subscribers = new Set();

function notifySubscribers() {
  for (const subscriber of subscribers) {
    subscriber(currentProxyStatus);
  }
}

export function getProxyStatus() {
  return currentProxyStatus;
}

export function subscribeProxyStatus(subscriber) {
  subscribers.add(subscriber);
  subscriber(currentProxyStatus);

  return () => {
    subscribers.delete(subscriber);
  };
}

export async function fetchProxyStatus() {
  try {
    const response = await fetch('/api/proxy/status');
    if (!response.ok) return currentProxyStatus;

    currentProxyStatus = await response.json();
    notifySubscribers();
  } catch (_) {}

  return currentProxyStatus;
}

export function startProxyStatusPolling({ intervalMs = 30000, immediate = true } = {}) {
  if (pollIntervalId !== null) return;

  if (immediate) {
    fetchProxyStatus();
  }

  pollIntervalId = window.setInterval(fetchProxyStatus, intervalMs);
}
