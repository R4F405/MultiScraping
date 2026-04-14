/* Sidebar collapse/expand — persists state in localStorage */
(function () {
  var KEY = 'ml_sidebar_collapsed';
  var COLLAPSED_W = 'var(--sidebar-collapsed-width)';
  var EXPANDED_W  = 'var(--sidebar-width)';

  function getSidebar() { return document.querySelector('.sidebar'); }
  function getMain()    { return document.querySelector('.shell-main'); }
  function getBtn()     { return document.getElementById('sidebar-toggle'); }

  function applyState(collapsed, animate) {
    var s = getSidebar();
    var m = getMain();
    if (!s) return;

    /* Disable transition on initial load so there's no animation flash */
    if (!animate) {
      s.style.transition = 'none';
      if (m) m.style.transition = 'none';
    }

    if (collapsed) {
      s.classList.add('collapsed');
      if (m) m.style.marginLeft = 'var(--sidebar-collapsed-width)';
    } else {
      s.classList.remove('collapsed');
      if (m) m.style.marginLeft = 'var(--sidebar-width)';
    }

    /* Re-enable transitions after first paint */
    if (!animate) {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          s.style.transition = '';
          if (m) m.style.transition = '';
        });
      });
    }

    var btn = getBtn();
    if (btn) {
      var icon = btn.querySelector('.toggle-icon');
      if (icon) icon.style.transform = collapsed ? 'rotate(180deg)' : 'rotate(0deg)';
      btn.setAttribute('title', collapsed ? 'Expandir sidebar' : 'Colapsar sidebar');
      btn.setAttribute('aria-label', collapsed ? 'Expandir sidebar' : 'Colapsar sidebar');
    }
  }

  function toggle() {
    var collapsed = !getSidebar().classList.contains('collapsed');
    localStorage.setItem(KEY, collapsed ? '1' : '0');
    applyState(collapsed, true);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var collapsed = localStorage.getItem(KEY) === '1';
    applyState(collapsed, false);

    var btn = getBtn();
    if (btn) btn.addEventListener('click', toggle);
  });
})();
