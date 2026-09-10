/* Редактор списка шагов: один и тот же для сценария сайта и для почтового
 * сервиса. Секции, список, форма шага и кнопки приходят снаружи, поэтому
 * оба раздела ведут себя одинаково, а инспектор умеет кидать шаги в любой из
 * них.
 */
'use strict';

import { $, esc, lines, toast } from './util.js';
import { actionOptions, actionSpec, defaultsFor, fieldOptions, state } from './state.js';

/* ---------- реестр мест, куда можно положить шаг ---------- */
const targets = new Map();

export function registerTarget(id, target) {
  targets.set(id, target);
}

export function getTarget(id) {
  return targets.get(id);
}

export function targetSelectHtml() {
  return [...targets.entries()].map(([id, target]) => {
    const options = target.sections()
      .map((section) => `<option value="${esc(id + '::' + section.path)}">${esc(section.label)}</option>`)
      .join('');
    return options ? `<optgroup label="${esc(target.label)}">${options}</optgroup>` : '';
  }).join('');
}

export function insertIntoTarget(value, step, mode) {
  const [id, path] = String(value || '').split('::');
  const target = targets.get(id);
  if (!target) return toast('Некуда добавлять: выберите сценарий или сервис', 'err');
  if (!target.ready()) return toast(target.notReady || 'Раздел не готов', 'err');
  target.insert(path, step, mode);
  toast('Шаг добавлен: ' + target.label.toLowerCase() + ' → ' + path, 'ok');
  return true;
}

/* ---------- поля формы шага ---------- */
function fieldHtml(field, value, prefix) {
  const id = prefix + field.name;
  const shared = `id="${id}" data-field="${esc(field.name)}" data-type="${esc(field.type)}"`;
  if (field.type === 'checkbox') {
    return `<label class="checkbox"><input type="checkbox" ${shared} ${value ? 'checked' : ''}>`
      + ` ${esc(field.label)}</label>`;
  }
  let control;
  if (field.type === 'select') {
    const options = fieldOptions(field).map((option) =>
      `<option value="${esc(option.value)}" ${String(value || '') === option.value ? 'selected' : ''}>`
      + `${esc(option.label)}</option>`).join('');
    control = `<select ${shared}>${options}</select>`;
  } else if (field.type === 'textarea') {
    control = `<textarea ${shared} rows="4">${esc(value)}</textarea>`;
  } else {
    const type = field.type === 'number' ? 'number' : 'text';
    control = `<input type="${type}" ${shared} value="${esc(value)}" `
      + `placeholder="${esc(field.placeholder || '')}">`;
  }
  return `<div class="field"><label for="${id}">${esc(field.label)}</label>${control}</div>`;
}

function selectorsText(step) {
  const value = step.selectors || step.selector || [];
  return (Array.isArray(value) ? value : [value]).join('\n');
}

function stepSummary(step) {
  const spec = actionSpec(step.action);
  const selector = Array.isArray(step.selectors) ? step.selectors[0] : step.selector;
  const parts = [];
  if (selector) parts.push(selector);
  (spec.fields || []).forEach((field) => {
    const value = step[field.name];
    if (value !== undefined && value !== '' && value !== false) parts.push(`${field.name}=${value}`);
  });
  if (step.note) parts.push('// ' + step.note);
  return parts.join('  ');
}

/* ---------- сам редактор ---------- */
export function createStepEditor(config) {
  const prefix = (config.prefix || 'sf') + '_';
  const editor = { path: config.sections()[0].path, index: -1 };

  const listEl = $(config.listEl);
  const formEl = $(config.formEl);
  const tabsEl = $(config.tabsEl);
  const hintEl = config.hintEl ? $(config.hintEl) : null;

  function section() {
    return config.sections().find((item) => item.path === editor.path) || config.sections()[0];
  }

  function list(path) {
    return config.list(path || editor.path) || [];
  }

  function current() {
    return list()[editor.index] || null;
  }

  function changed() {
    if (config.onChange) config.onChange();
  }

  function renderTabs() {
    tabsEl.innerHTML = config.sections().map((item) => {
      const size = (config.list(item.path) || []).length;
      return `<button data-path="${esc(item.path)}" class="${item.path === editor.path ? 'on' : ''}">`
        + `${esc(item.label)} <span class="count">${size}</span></button>`;
    }).join('');
    if (hintEl) hintEl.textContent = section().hint || '';
  }

  function render() {
    if (!config.doc()) {
      tabsEl.innerHTML = '';
      listEl.innerHTML = `<li class="empty">${esc(config.emptyText || 'Ничего не выбрано.')}</li>`;
      formEl.innerHTML = `<p class="hint">${esc(config.emptyText || '')}</p>`;
      if (hintEl) hintEl.textContent = '';
      return;
    }
    renderTabs();
    const steps = list();
    listEl.innerHTML = steps.length
      ? steps.map((step, index) => {
        const spec = actionSpec(step.action);
        const flavour = spec.group === 'mail' ? ' mail' : (spec.group === 'guard' ? ' guard' : '');
        return `<li data-index="${index}" class="${index === editor.index ? 'on' : ''}${flavour}">
          <span class="num">${index + 1}</span>
          <span class="act">${esc(step.action || '?')}</span>
          <span class="rest">${esc(stepSummary(step))}</span></li>`;
      }).join('')
      : '<li class="empty">Секция пуста — добавьте шаг ниже.</li>';
    renderForm();
  }

  function renderForm() {
    const step = current();
    if (!step) {
      formEl.innerHTML = '<p class="hint">Выберите шаг в списке или добавьте новый.</p>';
      return;
    }
    const spec = actionSpec(step.action);
    let html = `<div class="field"><label for="${prefix}action">Действие</label>`
      + `<select id="${prefix}action">${actionOptions(config.actionFilter)}</select></div>`;
    if (spec.hint) html += `<p class="hint">${esc(spec.hint)}</p>`;
    if (spec.selector !== 'none') {
      html += `<div class="field"><label for="${prefix}selectors">Селекторы `
        + `(по одному в строке, первый — основной)</label>`
        + `<textarea id="${prefix}selectors" rows="3">${esc(selectorsText(step))}</textarea></div>`;
    }
    (spec.fields || []).forEach((field) => { html += fieldHtml(field, step[field.name], prefix); });
    if (spec.selector !== 'none') {
      html += fieldHtml(state.catalog.timeout_field, step.timeout_ms, prefix);
    }
    html += fieldHtml(state.catalog.note_field, step.note, prefix);
    const placeholders = config.placeholders ? config.placeholders() : state.catalog.placeholders;
    html += `<p class="hint">Подстановки: ${esc(placeholders.join('  '))}</p>`;
    formEl.innerHTML = html;
    const actionSelect = $(prefix + 'action');
    actionSelect.value = step.action;
    actionSelect.addEventListener('change', changeAction);
    formEl.querySelectorAll(`[data-field], #${prefix}selectors`).forEach((node) => {
      node.addEventListener('change', collect);
    });
  }

  function changeAction() {
    const step = current();
    if (!step) return;
    const action = $(prefix + 'action').value;
    const kept = { action };
    const selectors = step.selectors || (step.selector ? [step.selector] : null);
    if (selectors && actionSpec(action).selector !== 'none') kept.selectors = selectors;
    if (step.note) kept.note = step.note;
    Object.keys(step).forEach((key) => delete step[key]);
    Object.assign(step, defaultsFor(action), kept);
    changed();
    render();
  }

  function collect() {
    const step = current();
    if (!step) return;
    const spec = actionSpec(step.action);
    if (spec.selector !== 'none') {
      delete step.selector;
      step.selectors = lines($(prefix + 'selectors').value);
    } else {
      delete step.selector;
      delete step.selectors;
    }
    formEl.querySelectorAll('[data-field]').forEach((node) => {
      const name = node.dataset.field;
      let value = null;
      if (node.dataset.type === 'checkbox') {
        value = node.checked ? true : null;
      } else if (node.dataset.type === 'number') {
        value = node.value.trim() === '' ? null : Number(node.value);
        if (Number.isNaN(value)) value = null;
      } else {
        value = node.value.trim() === '' ? null : node.value;
      }
      if (value === null) delete step[name];
      else step[name] = value;
    });
    changed();
    render();
  }

  function insert(step, path, mode) {
    const target = path || editor.path;
    const steps = list(target);
    const at = mode === 'after' && editor.index >= 0 && target === editor.path
      ? editor.index + 1 : steps.length;
    steps.splice(at, 0, step);
    if (target === editor.path) editor.index = at;
    changed();
    render();
    return at;
  }

  /* ---------- события ---------- */
  listEl.addEventListener('click', (event) => {
    const item = event.target.closest('li[data-index]');
    if (!item) return;
    editor.index = Number(item.dataset.index);
    render();
  });

  tabsEl.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-path]');
    if (!button) return;
    editor.path = button.dataset.path;
    editor.index = -1;
    render();
  });

  const buttons = config.buttons || {};
  const guard = (handler) => () => {
    if (!config.doc()) return toast(config.emptyText || 'Сначала выберите объект', 'err');
    handler();
  };

  if (buttons.addAfter) {
    $(buttons.addAfter).onclick = guard(() => insert(defaultsFor($(config.newActionEl).value), null, 'after'));
  }
  if (buttons.addEnd) {
    $(buttons.addEnd).onclick = guard(() => insert(defaultsFor($(config.newActionEl).value), null, 'end'));
  }
  if (buttons.del) {
    $(buttons.del).onclick = guard(() => {
      if (editor.index < 0) return;
      list().splice(editor.index, 1);
      editor.index = -1;
      changed();
      render();
    });
  }
  if (buttons.dup) {
    $(buttons.dup).onclick = guard(() => {
      const step = current();
      if (!step) return;
      insert(JSON.parse(JSON.stringify(step)), null, 'after');
    });
  }
  if (buttons.up) {
    $(buttons.up).onclick = guard(() => {
      const steps = list();
      if (editor.index <= 0) return;
      [steps[editor.index - 1], steps[editor.index]] = [steps[editor.index], steps[editor.index - 1]];
      editor.index -= 1;
      changed();
      render();
    });
  }
  if (buttons.down) {
    $(buttons.down).onclick = guard(() => {
      const steps = list();
      if (editor.index < 0 || editor.index >= steps.length - 1) return;
      [steps[editor.index + 1], steps[editor.index]] = [steps[editor.index], steps[editor.index + 1]];
      editor.index += 1;
      changed();
      render();
    });
  }
  if (buttons.move && config.moveSelectEl) {
    $(buttons.move).onclick = guard(() => {
      const step = current();
      if (!step) return toast('Выберите шаг', 'err');
      const target = $(config.moveSelectEl).value;
      if (target === editor.path) return;
      list().splice(editor.index, 1);
      list(target).push(step);
      editor.index = -1;
      changed();
      render();
      toast('Шаг перенесён в ' + target, 'ok');
    });
  }

  return {
    render,
    insert,
    current,
    path: () => editor.path,
    index: () => editor.index,
    reset(path) {
      editor.path = path || config.sections()[0].path;
      editor.index = -1;
    },
    fillActionSelect() {
      $(config.newActionEl).innerHTML = actionOptions(config.actionFilter);
      if (config.moveSelectEl) {
        $(config.moveSelectEl).innerHTML = config.sections()
          .map((item) => `<option value="${esc(item.path)}">${esc(item.label)}</option>`).join('');
      }
    }
  };
}
