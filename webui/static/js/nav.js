/* Переключение разделов панели. Выбранный раздел запоминается, чтобы после
   перезагрузки вернуться туда же. */
'use strict';

const KEY = 'webui-view';
const handlers = new Map();

export function onView(name, handler) {
  handlers.set(name, handler);
}

export function showView(name) {
  const button = document.querySelector(`#viewNav button[data-view="${name}"]`);
  if (!button) return;
  document.querySelectorAll('#viewNav button').forEach((node) => {
    node.classList.toggle('on', node === button);
  });
  document.querySelectorAll('main .view').forEach((node) => {
    node.classList.toggle('on', node.dataset.view === name);
  });
  localStorage.setItem(KEY, name);
  const handler = handlers.get(name);
  if (handler) handler();
}

export function initNav() {
  document.getElementById('viewNav').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-view]');
    if (button) showView(button.dataset.view);
  });
  // Alt+цифра — быстрый переход между разделами.
  document.addEventListener('keydown', (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    const index = Number(event.key) - 1;
    const buttons = [...document.querySelectorAll('#viewNav button')];
    if (index >= 0 && index < buttons.length) {
      event.preventDefault();
      showView(buttons[index].dataset.view);
    }
  });
  const saved = localStorage.getItem(KEY);
  showView(saved && document.querySelector(`#viewNav button[data-view="${saved}"]`)
    ? saved : 'overview');
}
