/**
 * sidebar.js — Collapsible sidebar + topbar clock controller
 *
 * Expects:
 *   - .sidebar element
 *   - #sidebarToggle button (in topbar)
 *   - data-page attribute on <body> or .nav-item[href] to set active item
 *   - #clock element for the live date/time display
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'itifaq-sidebar-collapsed';

  // ── Sidebar toggle ───────────────────────────────────────────
  function initSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const toggleBtn = document.getElementById('sidebarToggle');
    const overlay = document.querySelector('.sidebar-overlay');
    if (!sidebar) return;

    // Restore saved state
    const savedCollapsed = localStorage.getItem(STORAGE_KEY) === 'true';
    if (savedCollapsed && window.innerWidth > 768) {
      sidebar.classList.add('collapsed');
    }

    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        if (window.innerWidth <= 768) {
          // Mobile: slide in/out
          sidebar.classList.toggle('open');
          if (overlay) overlay.classList.toggle('visible');
        } else {
          // Desktop: collapse/expand
          const isCollapsed = sidebar.classList.toggle('collapsed');
          localStorage.setItem(STORAGE_KEY, isCollapsed);
        }
      });
    }

    // Close sidebar on overlay click (mobile)
    if (overlay) {
      overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
      });
    }
  }

  // ── Active nav item ──────────────────────────────────────────
  function setActiveNavItem() {
    const path = window.location.pathname;
    document.querySelectorAll('.nav-item[href]').forEach(item => {
      const href = item.getAttribute('href');
      if (href && path.startsWith(href) && href !== '/') {
        item.classList.add('active');
      } else if (href === '/' && path === '/') {
        item.classList.add('active');
      }
    });
  }

  // ── Clock ────────────────────────────────────────────────────
  function initClock() {
    const clockEl = document.getElementById('clock');
    if (!clockEl) return;

    function update() {
      const now = new Date();
      const date = now.toLocaleDateString('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric'
      });
      const time = now.toLocaleTimeString('en-GB', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
      clockEl.textContent = `${date}  ${time}`;
    }

    update();
    setInterval(update, 1000);
  }

  // ── Logout handler ───────────────────────────────────────────
  function initLogout() {
    document.querySelectorAll('[data-logout]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await fetch('/auth/logout', { method: 'POST' });
        } finally {
          window.location.href = '/auth/login';
        }
      });
    });
  }

  // ── Init ─────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    initSidebar();
    setActiveNavItem();
    initClock();
    initLogout();
  });

})();
