export function safeText(value, fallback = '—') {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text ? text : fallback;
}

export function toSafeHttpUrl(value) {
  if (!value) return null;
  try {
    const parsed = new URL(String(value), window.location.origin);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.href;
  } catch (_) {
    return null;
  }
}

export function emailStatusClass(status) {
  if (status === 'valid') return 'text-green-600 font-medium';
  if (status === 'invalid') return 'text-red-500';
  return 'text-slate-400';
}
