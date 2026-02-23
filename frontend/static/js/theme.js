/**
 * theme.js — Dark / light mode toggle
 *
 * - Persists preference in localStorage
 * - Reads system preference as default if no saved pref
 * - Updates the <html data-theme> attribute
 * - Updates all toggle button labels on the page
 */

(function () {
  'use strict';

  const STORAGE_KEY = 'itifaq-theme';

  // ── Init ──────────────────────────────────────────────────────
  function getPreferredTheme() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
    // Fall back to system preference
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);
    _updateButtons(theme);
  }

  function _updateButtons(theme) {
    const label = theme === 'dark' ? 'Light' : 'Dark';
    document.querySelectorAll('[data-theme-btn], .theme-toggle-btn, .theme-btn, #themeBtn')
      .forEach(btn => { btn.textContent = label; });
  }

  // ── Public toggle ─────────────────────────────────────────────
  window.toggleTheme = function () {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  };

  window.getCurrentTheme = function () {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  };

  // ── Apply on load ─────────────────────────────────────────────
  applyTheme(getPreferredTheme());

  // Watch for system preference changes
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', e => {
    if (!localStorage.getItem(STORAGE_KEY)) {
      applyTheme(e.matches ? 'light' : 'dark');
    }
  });
})();
