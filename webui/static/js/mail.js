/* Раздел «Почта»: встроенные сервисы и свои, описанные шагами.
 *
 * Свой сервис — это адрес страницы, правила чтения адреса/кода/ссылки и три
 * списка шагов: создать ящик, обновить список писем, открыть письмо. Шаги
 * редактируются тем же редактором, что и шаги сценария, поэтому селекторы для
 * них можно снимать инспектором.
 */
'use strict';

import { $, api, esc, lines, number, sendJson, toast } from './util.js';
import { state } from './state.js';
import { createStepEditor, registerTarget } from './steps.js';

const SECTIONS = [
  { path: 'create', label: 'Создать ящик',
    hint: 'Шаги от открытия страницы до появления адреса: нажать «Создать», ввести имя.' },
  { path: 'refresh', label: 'Обновить письма',
    hint: 'Что нажать, чтобы список писем обновился. Выполняется в цикле ожидания.' },
  { path: 'open_message', label: 'Открыть письмо',
    hint: 'Клик по первому (свежему) письму: из его текста берётся код или ссылка.' }
];

const MAIL_PLACEHOLDERS = ['{username}', '{login}'];
const service = { key: null, data: null, dirty: false };

function listByPath(path) {
  if (!service.data) return [];
  service.data[path] = service.data[path] || [];
  return service.data[path];
}

function markDirty() {
  service.dirty = true;
  $('mailDirty').textContent = 'Есть несохранённые правки.';
}

const editor = createStepEditor({
  prefix: 'mf',
  listEl: 'mailStepList',
  formEl: 'mailStepForm',
  tabsEl: 'mailSectionTabs',
  hintEl: 'mailSectionHint',
  newActionEl: 'mailNewAction',
  sections: () => SECTIONS,
  list: listByPath,
  doc: () => service.data,
  onChange: markDirty,
  emptyText: 'Выберите свой почтовый сервис или добавьте новый.',
  placeholders: () => MAIL_PLACEHOLDERS,
  // Почтовые действия сами живут внутри почтового клиента — здесь только
  // работа со страницей сервиса.
  actionFilter: (action) => action.group !== 'mail',
  buttons: {
    up: 'btnMailStepUp', down: 'btnMailStepDown', dup: 'btnMailStepDuplicate',
    del: 'btnMailStepDelete', addAfter: 'btnMailAddAfter', addEnd: 'btnMailAddEnd'
  }
});

export function currentMailService() {
  return service.data;
}

export async function refreshMailServices() {
  const data = await api('/api/mail/services');
  state.mailServices = await api('/api/mail-services');
  renderList(data.services);
  fillMailSelects();
  const custom = data.services.filter((item) => item.kind === 'custom');
  $('mailPick').innerHTML = custom.length
    ? custom.map((item) => `<option value="${esc(item.id)}">${esc(item.label)} — ${item.steps} шагов</option>`).join('')
    : '<option value="">свои сервисы пока не добавлены</option>';
  if (service.key && custom.some((item) => item.id === service.key)) $('mailPick').value = service.key;
  return data;
}

function renderList(services) {
  $('mailList').innerHTML = services.map((item) => {
    const kind = item.kind === 'custom' ? 'свой' : (item.kind === 'builtin' ? 'встроенный' : '—');
    const steps = item.kind === 'custom' ? ` · шагов: ${item.steps}` : '';
    return `<div class="service ${item.kind}">
      <div class="row"><b>${esc(item.label)}</b><span class="pill">${esc(kind)}</span></div>
      <div class="hint">${esc(item.hint || '')}${esc(steps)}</div>
      <div class="mono muted">${esc(item.url || '')}</div>
    </div>`;
  }).join('');
}

/* Выпадающие списки почты в запуске и в сценарии. */
export function fillMailSelects() {
  const all = state.mailServices;
  $('runMail').innerHTML = all
    .map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  const editable = all.filter((item) => item.id !== 'auto')
    .map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  const keepEd = $('edMail').value;
  $('edMail').innerHTML = editable;
  $('newMail').innerHTML = editable;
  if (keepEd) $('edMail').value = keepEd;
}

export async function loadMailService(name) {
  service.data = await api('/api/mail/services/' + encodeURIComponent(name));
  service.key = name;
  service.dirty = false;
  $('mailEditor').hidden = false;
  $('mailDirty').textContent = 'Сохранено.';
  $('mailFile').textContent = 'mail_services/' + name + '.json';
  $('mailName').value = service.data.name || name;
  $('mailLabel').value = service.data.label || '';
  $('mailUrl').value = service.data.url || '';
  $('mailPoll').value = service.data.poll_interval || 3;
  const address = service.data.address || {};
  $('mailAddressSource').value = address.source || 'value';
  $('mailAddressSelectors').value = (address.selectors || []).join('\n');
  $('mailAddressAttribute').value = address.attribute || '';
  $('mailAddressTimeout').value = address.timeout || 60;
  const code = service.data.code || {};
  $('mailCodeLength').value = code.length || 6;
  $('mailCodeKeywords').value = code.keywords || '';
  const link = service.data.link || {};
  $('mailLinkKeywords').value = link.keywords || '';
  $('mailLinkExclude').value = link.exclude || '';
  editor.reset('create');
  editor.render();
}

function payload() {
  const data = service.data;
  data.name = $('mailName').value.trim() || service.key;
  data.label = $('mailLabel').value.trim() || data.name;
  data.url = $('mailUrl').value.trim();
  data.poll_interval = number($('mailPoll').value, 3);
  data.address = {
    source: $('mailAddressSource').value,
    selectors: lines($('mailAddressSelectors').value),
    attribute: $('mailAddressAttribute').value.trim(),
    timeout: number($('mailAddressTimeout').value, 60)
  };
  data.code = {
    length: number($('mailCodeLength').value, 6),
    keywords: $('mailCodeKeywords').value.trim()
  };
  data.link = {
    keywords: $('mailLinkKeywords').value.trim(),
    exclude: $('mailLinkExclude').value.trim()
  };
  return data;
}

export async function saveMailService() {
  if (!service.data) return;
  try {
    const result = await sendJson('/api/mail/services/' + encodeURIComponent(service.key),
      'PUT', payload());
    service.key = result.name || service.key;
    service.dirty = false;
    $('mailDirty').textContent = 'Сохранено.';
    await refreshMailServices();
    $('mailPick').value = service.key;
    toast('Почтовый сервис сохранён', 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

export function initMail(openInspector) {
  editor.fillActionSelect();

  registerTarget('mail', {
    label: 'Почтовый сервис',
    notReady: 'Сначала выберите свой сервис в разделе «Почта»',
    sections: () => SECTIONS.map((item) => ({ path: item.path, label: 'почта: ' + item.label })),
    ready: () => Boolean(service.data),
    insert: (path, step, mode) => editor.insert(step, path, mode),
    currentStep: () => editor.current(),
    render: () => editor.render(),
    markDirty
  });

  $('mailPick').onchange = (event) => {
    if (!event.target.value) return;
    if (service.dirty && !confirm('Несохранённые правки будут потеряны. Продолжить?')) {
      $('mailPick').value = service.key;
      return;
    }
    loadMailService(event.target.value).catch((error) => toast(error.message, 'err'));
  };

  $('btnMailReload').onclick = () => refreshMailServices()
    .catch((error) => toast(error.message, 'err'));

  $('btnMailNew').onclick = () => {
    $('mailNewName').value = '';
    $('mailNewUrl').value = '';
    $('mailNewCopyHint').hidden = true;
    $('dlgMailNew').returnValue = '';
    $('dlgMailNew').dataset.copyFrom = '';
    $('dlgMailNew').showModal();
  };

  $('btnMailCopy').onclick = () => {
    if (!service.key) return toast('Сначала выберите свой сервис', 'err');
    $('mailNewName').value = service.key + '-copy';
    $('mailNewUrl').value = $('mailUrl').value;
    $('dlgMailNew').dataset.copyFrom = service.key;
    $('mailNewCopyHint').hidden = false;
    $('mailNewCopyHint').textContent = 'Шаги скопируются из «' + service.key + '».';
    $('dlgMailNew').showModal();
  };

  $('dlgMailNew').addEventListener('close', async () => {
    if ($('dlgMailNew').returnValue !== 'create') return;
    const body = {
      name: $('mailNewName').value.trim(),
      url: $('mailNewUrl').value.trim(),
      template: $('mailNewTemplate').value,
      copy_from: $('dlgMailNew').dataset.copyFrom || null
    };
    if (!body.name) return toast('Нужно имя сервиса', 'err');
    try {
      const result = await sendJson('/api/mail/services', 'POST', body);
      await refreshMailServices();
      $('mailPick').value = result.name;
      await loadMailService(result.name);
      toast('Создан ' + result.file, 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  });

  $('btnMailDelete').onclick = async () => {
    if (!service.key) return toast('Сначала выберите свой сервис', 'err');
    if (!confirm(`Удалить сервис «${service.key}»? Файл переедет в mail_services/_trash.`)) return;
    try {
      const result = await api('/api/mail/services/' + encodeURIComponent(service.key),
        { method: 'DELETE' });
      service.data = null;
      service.key = null;
      $('mailEditor').hidden = true;
      await refreshMailServices();
      toast('Перенесён в ' + result.trash, 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('btnMailSave').onclick = saveMailService;

  $('btnMailValidate').onclick = async () => {
    if (!service.data) return;
    try {
      const result = await sendJson('/api/mail/services/validate', 'POST', payload());
      toast(result.errors.length ? 'Ошибки: ' + result.errors.join('; ') : 'Описание корректно',
        result.errors.length ? 'err' : 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('btnMailInspect').onclick = () => {
    const url = $('mailUrl').value.trim();
    if (!url) return toast('Сначала укажите адрес страницы почты', 'err');
    openInspector(url, 'mail::create');
  };

  ['mailName', 'mailLabel', 'mailUrl', 'mailPoll', 'mailAddressSelectors',
    'mailAddressAttribute', 'mailAddressTimeout', 'mailCodeLength', 'mailCodeKeywords',
    'mailLinkKeywords', 'mailLinkExclude'].forEach((id) => {
    $(id).addEventListener('input', markDirty);
  });
  $('mailAddressSource').addEventListener('change', markDirty);
}
