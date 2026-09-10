/* Раздел «Обзор»: схема работы и короткий чеклист готовности. */
'use strict';

import { $, api, esc } from './util.js';
import { state } from './state.js';
import { showView } from './nav.js';

const STEPS = [
  {
    id: 'scenario',
    title: 'Сценарий сайта',
    view: 'scenario',
    ready: () => state.scenarios.length > 0,
    okText: (data) => `${data.scenarios} шт. — можно запускать`,
    text: 'Опишите шаги регистрации: поля, кнопку, ожидание письма.'
  },
  {
    id: 'mail',
    title: 'Почта',
    view: 'mail',
    ready: () => true,
    okText: (data) => data.mail_services
      ? `встроенные + свои: ${data.mail_services}`
      : 'встроенные tmail и Mail.tm',
    text: 'Свой сервис добавляется шагами: создать ящик, обновить, открыть письмо.'
  },
  {
    id: 'proxy',
    title: 'Прокси',
    view: 'proxy',
    ready: () => (state.environment.proxies || 0) > 0,
    okText: (data) => `${data.proxies} адресов, ротация настраивается`,
    text: 'Не обязательно: без списка запуск идёт напрямую.'
  },
  {
    id: 'browser',
    title: 'Браузер',
    view: 'run',
    ready: () => true,
    okText: (data) => data.adspower ? 'AdsPower подключён' : 'обычный Chrome',
    text: 'AdsPower подхватывается автоматически, если рядом лежит adspower.local.json.'
  },
  {
    id: 'captcha',
    title: 'Капча',
    view: 'run',
    ready: () => Boolean(state.settings && state.settings.captcha.enabled),
    okText: () => {
      const mode = state.settings ? state.settings.captcha.mode : 'wait';
      return { wait: 'ждём человека', pause: 'выждать паузу', stop: 'пропустить аккаунт',
        log: 'только лог' }[mode] || mode;
    },
    text: 'Программа не решает капчу: она ставит паузу и зовёт вас.'
  }
];

export function renderChecklist() {
  const data = state.environment || {};
  const counts = Object.assign({ scenarios: state.scenarios.length }, data);
  $('checklist').innerHTML = STEPS.map((item) => {
    const ready = item.ready();
    return `<div class="check ${ready ? 'ok' : 'todo'}">
      <span class="mark">${ready ? '✓' : '—'}</span>
      <div>
        <div class="row"><b>${esc(item.title)}</b>
          <span class="hint">${esc(ready ? item.okText(counts) : 'нужно настроить')}</span></div>
        <div class="hint">${esc(item.text)}</div>
      </div>
      <button class="quiet small" data-goto="${esc(item.view)}">Открыть</button>
    </div>`;
  }).join('');
}

export function initOverview(refreshEnvironment) {
  $('checklist').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-goto]');
    if (button) showView(button.dataset.goto);
  });
  $('btnChecklist').onclick = async () => {
    await refreshEnvironment();
    renderChecklist();
  };
}
