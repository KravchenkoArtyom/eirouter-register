/* Раздел «Сценарий»: выбор файла, поля сайта и редактор шагов. */
'use strict';

import { $, api, esc, sendJson, toast } from './util.js';
import { state } from './state.js';
import { createStepEditor, registerTarget } from './steps.js';

/* Порядок секций совпадает с порядком исполнения — так их и подписываем. */
const SECTIONS = [
  { path: 'steps', label: '1 · Форма', hint: 'Заполнение полей до отправки формы.' },
  { path: 'verification.send', label: '2 · Запрос кода',
    hint: 'Например, клик по «Отправить код на почту».' },
  { path: 'verification.wait', label: '3 · Ожидание письма',
    hint: 'Если секция пуста, регистратор всё равно ждёт код из письма.' },
  { path: 'verification.complete', label: '4 · Ввод кода',
    hint: 'Подстановка {code} и подтверждение.' },
  { path: 'submit', label: '5 · Отправка', hint: 'Кнопка регистрации.' },
  { path: 'success', label: '6 · После успеха',
    hint: 'Что сделать после: открыть ссылку из письма, дождаться адреса.' }
];

const scenario = { key: null, data: null, dirty: false };

function listByPath(path) {
  if (!scenario.data) return [];
  if (path.startsWith('verification.')) {
    const sub = path.split('.')[1];
    scenario.data.verification = scenario.data.verification || {};
    scenario.data.verification[sub] = scenario.data.verification[sub] || [];
    return scenario.data.verification[sub];
  }
  scenario.data[path] = scenario.data[path] || [];
  return scenario.data[path];
}

function markDirty() {
  scenario.dirty = true;
  renderDirty();
}

function renderDirty() {
  $('edDirty').textContent = scenario.dirty
    ? 'Есть несохранённые правки.'
    : (scenario.key ? 'Сохранено.' : '');
}

const editor = createStepEditor({
  prefix: 'sf',
  listEl: 'stepList',
  formEl: 'stepForm',
  tabsEl: 'sectionTabs',
  hintEl: 'sectionHint',
  newActionEl: 'newAction',
  moveSelectEl: 'moveSection',
  sections: () => SECTIONS,
  list: listByPath,
  doc: () => scenario.data,
  onChange: markDirty,
  emptyText: 'Сначала выберите сценарий.',
  buttons: {
    up: 'btnStepUp', down: 'btnStepDown', dup: 'btnStepDuplicate', del: 'btnStepDelete',
    addAfter: 'btnAddAfter', addEnd: 'btnAddEnd', move: 'btnStepMove'
  }
});

export function currentScenario() {
  return scenario.data;
}

export async function refreshScenarios() {
  state.scenarios = await api('/api/scenarios');
  const options = state.scenarios.map((item) => {
    const mail = item.mail_steps ? `, почтовых ${item.mail_steps}` : '';
    return `<option value="${esc(item.name)}">${esc(item.name)} — ${item.steps} шагов${mail}</option>`;
  }).join('');
  const runValue = $('runScenario').value;
  const editValue = $('edScenario').value;
  $('runScenario').innerHTML = options;
  $('edScenario').innerHTML = options;
  if (runValue) $('runScenario').value = runValue;
  if (editValue) $('edScenario').value = editValue;
}

export async function loadScenario(name) {
  scenario.data = await api('/api/scenarios/' + encodeURIComponent(name));
  scenario.key = name;
  scenario.dirty = false;
  editor.reset('steps');
  $('edName').value = scenario.data.name || name;
  $('edUrl').value = scenario.data.url || '';
  $('edMail').value = scenario.data.mail || 'tmail';
  renderDirty();
  editor.render();
}

function payload() {
  const data = scenario.data;
  data.name = $('edName').value.trim() || scenario.key;
  data.url = $('edUrl').value.trim();
  data.mail = $('edMail').value;
  return data;
}

export async function saveScenario() {
  if (!scenario.data) return;
  try {
    const result = await sendJson('/api/scenarios/' + encodeURIComponent(scenario.key),
      'PUT', payload());
    scenario.key = result.name;
    scenario.dirty = false;
    renderDirty();
    await refreshScenarios();
    $('edScenario').value = scenario.key;
    toast('Сценарий сохранён', 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

export function initScenarios() {
  editor.fillActionSelect();

  registerTarget('scenario', {
    label: 'Сценарий сайта',
    notReady: 'Сначала выберите сценарий в разделе «Сценарий»',
    sections: () => SECTIONS.map((item) => ({ path: item.path, label: item.label })),
    ready: () => Boolean(scenario.data),
    insert: (path, step, mode) => editor.insert(step, path, mode),
    currentStep: () => editor.current(),
    render: () => editor.render(),
    markDirty
  });

  $('btnSaveScenario').onclick = saveScenario;

  $('btnValidate').onclick = async () => {
    if (!scenario.data) return;
    try {
      const result = await sendJson('/api/scenarios/validate', 'POST', payload());
      toast(result.errors.length ? 'Ошибки: ' + result.errors.join('; ') : 'Сценарий корректен',
        result.errors.length ? 'err' : 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('btnNewScenario').onclick = () => {
    $('newName').value = '';
    $('newUrl').value = '';
    $('newCopyHint').hidden = true;
    $('dlgNew').returnValue = '';
    $('dlgNew').dataset.copyFrom = '';
    $('dlgNew').showModal();
  };

  $('btnCopyScenario').onclick = () => {
    if (!scenario.key) return toast('Сначала выберите сценарий', 'err');
    $('newName').value = scenario.key + '-copy';
    $('newUrl').value = $('edUrl').value;
    $('dlgNew').dataset.copyFrom = scenario.key;
    $('newCopyHint').hidden = false;
    $('newCopyHint').textContent = 'Шаги будут скопированы из «' + scenario.key
      + '»; заготовка не применяется.';
    $('dlgNew').showModal();
  };

  $('dlgNew').addEventListener('close', async () => {
    if ($('dlgNew').returnValue !== 'create') return;
    const body = {
      name: $('newName').value.trim(),
      url: $('newUrl').value.trim(),
      mail: $('newMail').value,
      template: $('newTemplate').value,
      copy_from: $('dlgNew').dataset.copyFrom || null
    };
    if (!body.name) return toast('Нужно имя сценария', 'err');
    try {
      const result = await sendJson('/api/scenarios', 'POST', body);
      await refreshScenarios();
      $('edScenario').value = result.name;
      await loadScenario(result.name);
      toast('Создан ' + result.file, 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  });

  $('btnDeleteScenario').onclick = async () => {
    if (!scenario.key) return;
    if (!confirm(`Удалить сценарий «${scenario.key}»? Файл переедет в universal_scenarios/_trash.`)) return;
    try {
      const result = await api('/api/scenarios/' + encodeURIComponent(scenario.key),
        { method: 'DELETE' });
      scenario.data = null;
      scenario.key = null;
      await refreshScenarios();
      if ($('edScenario').value) await loadScenario($('edScenario').value);
      else editor.render();
      toast('Перенесён в ' + result.trash, 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('edScenario').onchange = (event) => {
    if (scenario.dirty && !confirm('Несохранённые правки будут потеряны. Продолжить?')) {
      $('edScenario').value = scenario.key;
      return;
    }
    loadScenario(event.target.value).catch((error) => toast(error.message, 'err'));
  };

  $('btnReloadScenarios').onclick = () => refreshScenarios()
    .catch((error) => toast(error.message, 'err'));
  ['edName', 'edUrl'].forEach((id) => { $(id).addEventListener('input', markDirty); });
  $('edMail').addEventListener('change', markDirty);
}
