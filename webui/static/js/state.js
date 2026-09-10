/* Общее состояние панели и работа с каталогом действий.
 *
 * Форма шага нигде не зашита в разметку: она собирается из /api/actions,
 * поэтому новое действие в universal/actions.py появляется в интерфейсе само.
 */
'use strict';

import { esc } from './util.js';

export const state = {
  catalog: null,
  mailServices: [],     // [{id, label, kind}] — для выпадающих списков
  scenarios: [],
  settings: null,
  options: { captcha_modes: [], rotations: [] },
  environment: {},
  job: null,
  logOffset: 0,
  poll: null,        // запасной опрос по таймеру
  stream: null,      // EventSource с потоком состояния запуска
  captchaSeq: 0,
  dropped: 0,     // сколько строк лога обрезано на сервере
  inspect: { sid: null, mode: 'mirror', element: null },
  buffer: []
};

export function actionSpec(name) {
  return (state.catalog.actions || []).find((item) => item.name === name)
    || { fields: [], selector: 'none' };
}

export function actionOptions(filter) {
  return state.catalog.groups.map((group) => {
    const options = state.catalog.actions
      .filter((action) => action.group === group.id && !action.hidden
        && (!filter || filter(action)))
      .map((action) => `<option value="${esc(action.name)}">${esc(action.label)}</option>`)
      .join('');
    return options ? `<optgroup label="${esc(group.label)}">${options}</optgroup>` : '';
  }).join('');
}

/* Поля со ссылкой на справочник заполняются на клиенте: список почтовых
   сервисов зависит от того, что пользователь добавил в mail_services/. */
export function fieldOptions(field) {
  if (field.source !== 'mail_services') return field.options || [];
  const services = state.mailServices
    .filter((item) => !['auto', 'none'].includes(item.id))
    .map((item) => ({ value: item.id, label: item.label }));
  return [{ value: '', label: 'как выбрано в запуске' }].concat(services);
}

export function defaultsFor(action) {
  const step = { action };
  if (actionSpec(action).selector !== 'none') step.selectors = [];
  if (action === 'wait') step.seconds = 2;
  if (action === 'dom') step.operation = 'remove';
  if (action === 'wait_random') { step.min_seconds = 1; step.max_seconds = 4; }
  if (action === 'captcha_wait') step.timeout = 300;
  if (action === 'notify') step.level = 'info';
  if (action === 'mail_wait_code' || action === 'wait_email_code') {
    step.length = 6;
    step.timeout = 180;
  }
  if (action === 'mail_wait_link' || action === 'mail_open_link') step.timeout = 300;
  if (action === 'mail_open_link') step.target = 'same_tab';
  return step;
}

export function buildStep(selector, action, value) {
  const step = defaultsFor(action);
  step.selectors = [selector];
  if (value && ['fill', 'type', 'select'].includes(action)) step.value = value;
  return step;
}
