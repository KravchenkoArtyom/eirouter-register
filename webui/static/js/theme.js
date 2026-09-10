/* Тема оформления: авто (по системе), светлая, тёмная. */
'use strict';

const KEY = 'webui-theme';
const systemDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

function choice() {
  const saved = localStorage.getItem(KEY);
  return ['auto', 'light', 'dark'].includes(saved) ? saved : 'auto';
}

function apply() {
  const current = choice();
  // «Авто» разрешаем здесь, чтобы в стилях осталось ровно две палитры.
  const resolved = current === 'auto' ? (systemDark && systemDark.matches ? 'dark' : 'light') : current;
  document.documentElement.dataset.theme = resolved;
  document.querySelectorAll('#themeSwitch button').forEach((node) => {
    node.classList.toggle('on', node.dataset.themeChoice === current);
  });
}

export function initTheme() {
  document.getElementById('themeSwitch').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-theme-choice]');
    if (!button) return;
    localStorage.setItem(KEY, button.dataset.themeChoice);
    apply();
  });
  if (systemDark && systemDark.addEventListener) {
    systemDark.addEventListener('change', () => { if (choice() === 'auto') apply(); });
  }
  apply();
}
