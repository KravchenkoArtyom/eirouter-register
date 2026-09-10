/* Раздел «Инспектор»: страница сайта или почты внутри панели.
 *
 * Зеркало — тот же DOM, отданный с нашего origin и без чужих скриптов:
 * наведение подсвечивает элемент, клик забирает селектор, Shift+клик кладёт
 * элемент в буфер. Полученный шаг уходит в сценарий или в почтовый сервис —
 * куда именно, выбирает список «Куда добавлять шаги».
 */
'use strict';

import { $, api, esc, sendJson, toast } from './util.js';
import { actionOptions, buildStep, state } from './state.js';
import { getTarget, insertIntoTarget, targetSelectHtml } from './steps.js';
import { showView } from './nav.js';
import { currentScenario } from './scenario.js';

function stageMode(mode) {
  state.inspect.mode = mode;
  document.querySelectorAll('#inspectModes button').forEach((node) => {
    node.classList.toggle('on', node.dataset.mode === mode);
  });
  $('mirror').hidden = mode !== 'mirror';
  $('shot').hidden = mode !== 'shot';
  if (state.inspect.sid) loadStage();
}

async function loadStage() {
  const sid = state.inspect.sid;
  if (!sid) return;
  if (state.inspect.mode === 'mirror') {
    $('mirror').src = `/api/inspect/${sid}/mirror?rev=${Date.now()}`;
  } else {
    try {
      const shot = await api(`/api/inspect/${sid}/shot`);
      $('shot').src = 'data:image/png;base64,' + shot.png;
    } catch (error) {
      toast(error.message, 'err');
    }
  }
}

export async function openInspector(url, target) {
  showView('inspector');
  if (url) $('inUrl').value = url;
  if (target) $('targetSection').value = target;
  await openSession();
}

async function openSession() {
  const scenario = currentScenario();
  const url = $('inUrl').value.trim() || (scenario && scenario.url) || '';
  if (!url) return toast('Укажите адрес страницы', 'err');
  $('btnInspectOpen').disabled = true;
  $('inspectStatus').textContent = 'Открываю ' + url + ' …';
  try {
    if (state.inspect.sid) {
      await api('/api/inspect/' + state.inspect.sid, { method: 'DELETE' }).catch(() => {});
    }
    const result = await sendJson('/api/inspect/start', 'POST', { url });
    state.inspect.sid = result.sid;
    $('btnMirrorReload').disabled = false;
    $('btnInspectClose').disabled = false;
    $('inspectUrl').textContent = result.state.url || url;
    $('inspectStatus').textContent = 'Наведение подсвечивает элемент, клик забирает селектор, '
      + 'Shift+клик кладёт в буфер.';
    await loadStage();
  } catch (error) {
    $('inspectStatus').textContent = 'Не открылось: ' + error.message;
    toast(error.message, 'err');
  } finally {
    $('btnInspectOpen').disabled = false;
  }
}

function showElement(element) {
  state.inspect.element = element;
  $('elementInfo').textContent = `<${element.tag}>` + (element.text ? ` «${element.text}»` : '');
  $('candList').innerHTML = (element.candidates || []).map((item, index) => {
    const flag = item.playwright ? 'PW' : (item.unique ? '1' : (item.count >= 0 ? item.count : '?'));
    return `<div data-selector="${esc(item.sel)}" class="${index === 0 ? 'on' : ''}">
      <span class="flag">[${esc(flag)}]</span> ${esc(item.sel)}</div>`;
  }).join('') || '<div class="flag">кандидатов нет</div>';
  const best = (element.candidates || []).find((item) => item.unique)
    || (element.candidates || [])[0];
  $('pickSelector').value = best ? best.sel : '';
  if (element.suggested) $('pickAction').value = element.suggested;
  $('pickValue').value = element.value || '';
  $('verifyResult').textContent = '';
}

function highlight(selector) {
  const frame = $('mirror');
  if (state.inspect.mode === 'mirror' && frame.contentWindow) {
    // Зеркало в песочнице: его origin — «null», поэтому цель сообщения '*'.
    frame.contentWindow.postMessage({ type: 'highlight', selector }, '*');
  }
}

/* ---------- буфер ---------- */
export async function loadBuffer() {
  state.buffer = await api('/api/buffer').catch(() => []);
  renderBuffer();
}

function renderBuffer() {
  $('bufferList').innerHTML = state.buffer.length
    ? state.buffer.map((item) => `<div class="item" data-id="${esc(item.id)}">
        <div class="row"><span class="tag">${esc(item.action || 'click')}</span>
          <span class="sel">${esc(item.selector)}</span>
          <button class="quiet" data-act="step">→ шаг</button>
          <button class="quiet" data-act="remove">убрать</button></div>
        ${item.value ? `<div class="tag">значение: ${esc(item.value)}</div>` : ''}
      </div>`).join('')
    : '<p class="hint">Буфер пуст. Shift+клик по элементу зеркала кладёт его сюда.</p>';
}

async function addToBuffer() {
  const selector = $('pickSelector').value.trim();
  if (!selector) return toast('Пустой селектор', 'err');
  const element = state.inspect.element || {};
  try {
    const item = await sendJson('/api/buffer', 'POST', {
      selector,
      action: $('pickAction').value,
      value: $('pickValue').value,
      tag: element.tag || '',
      text: element.text || '',
      url: element.url || $('inspectUrl').textContent
    });
    state.buffer.push(item);
    renderBuffer();
    toast('Элемент в буфере', 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

export function refreshTargets() {
  const keep = $('targetSection').value;
  $('targetSection').innerHTML = targetSelectHtml();
  if (keep) $('targetSection').value = keep;
}

export function initInspector() {
  $('pickAction').innerHTML = actionOptions((action) => action.selector === 'required');
  refreshTargets();

  $('inspectModes').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]');
    if (button) stageMode(button.dataset.mode);
  });

  $('btnInspectOpen').onclick = openSession;
  $('btnMirrorReload').onclick = () => loadStage();

  $('btnInspectClose').onclick = async () => {
    if (!state.inspect.sid) return;
    await api('/api/inspect/' + state.inspect.sid, { method: 'DELETE' }).catch(() => {});
    state.inspect.sid = null;
    $('mirror').removeAttribute('src');
    $('shot').removeAttribute('src');
    $('btnMirrorReload').disabled = true;
    $('btnInspectClose').disabled = true;
    $('inspectStatus').textContent = 'Сессия закрыта.';
  };

  window.addEventListener('message', (event) => {
    // Зеркало в песочнице приходит с origin «null», поэтому проверяем не
    // origin, а сам источник: это должен быть наш iframe.
    if (event.source !== $('mirror').contentWindow) return;
    const data = event.data || {};
    if (data.source !== 'webui-picker') return;
    if (data.type === 'hover') {
      $('inspectHover').textContent = data.payload.path || '';
    } else if (data.type === 'ready') {
      $('inspectUrl').textContent = data.payload.url || '';
    } else if (data.type === 'pick' || data.type === 'save') {
      showElement(data.payload);
      if (data.type === 'save') addToBuffer();
    }
  });

  $('candList').addEventListener('click', (event) => {
    const item = event.target.closest('div[data-selector]');
    if (!item) return;
    $('candList').querySelectorAll('div').forEach((node) => node.classList.toggle('on', node === item));
    $('pickSelector').value = item.dataset.selector;
    highlight(item.dataset.selector);
  });

  $('btnVerify').onclick = async () => {
    const selector = $('pickSelector').value.trim();
    if (!state.inspect.sid || !selector) return;
    try {
      const result = await sendJson(`/api/inspect/${state.inspect.sid}/verify`, 'POST', { selector });
      $('verifyResult').textContent = result.count
        ? `Найдено ${result.count}${result.unique ? ' (уникален)' : ''}, `
          + `видим: ${result.visible ? 'да' : 'нет'}` + (result.tag ? `, <${result.tag}>` : '')
        : 'На настоящей странице ничего не найдено.';
      if (state.inspect.mode === 'shot') {
        const shot = await api(`/api/inspect/${state.inspect.sid}/shot?selector=`
          + encodeURIComponent(selector));
        $('shot').src = 'data:image/png;base64,' + shot.png;
      } else {
        highlight(selector);
      }
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('shot').addEventListener('click', async (event) => {
    if (!state.inspect.sid || state.inspect.mode !== 'shot') return;
    const rect = $('shot').getBoundingClientRect();
    try {
      const result = await sendJson(`/api/inspect/${state.inspect.sid}/pick`, 'POST', {
        x: event.clientX - rect.left, y: event.clientY - rect.top,
        dw: rect.width, dh: rect.height
      });
      if (!result.element) return toast('Элемент не распознан', 'err');
      showElement(Object.assign({ value: '' }, result.element));
    } catch (error) {
      toast(error.message, 'err');
    }
  });

  $('btnAct').onclick = async () => {
    if (!state.inspect.sid) return toast('Инспектор не открыт', 'err');
    const action = $('actAction').value;
    const body = { action, selector: $('pickSelector').value.trim(), value: $('actValue').value };
    try {
      const result = await sendJson(`/api/inspect/${state.inspect.sid}/act`, 'POST', body);
      $('inspectUrl').textContent = result.url || '';
      await loadStage();
      toast('Готово: ' + action, 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('bufferList').addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-act]');
    if (!button) return;
    const id = button.closest('.item').dataset.id;
    const item = state.buffer.find((entry) => entry.id === id);
    if (!item) return;
    if (button.dataset.act === 'remove') {
      await api('/api/buffer/' + id, { method: 'DELETE' }).catch((error) => toast(error.message, 'err'));
      state.buffer = state.buffer.filter((entry) => entry.id !== id);
      renderBuffer();
    } else {
      insertIntoTarget($('targetSection').value,
        buildStep(item.selector, item.action || 'click', item.value), $('insertMode').value);
    }
  });

  $('btnToBuffer').onclick = addToBuffer;

  $('btnBufferAll').onclick = () => {
    if (!state.buffer.length) return;
    state.buffer.forEach((item) => insertIntoTarget($('targetSection').value,
      buildStep(item.selector, item.action || 'click', item.value), 'end'));
  };

  $('btnBufferClear').onclick = async () => {
    await api('/api/buffer', { method: 'DELETE' }).catch((error) => toast(error.message, 'err'));
    state.buffer = [];
    renderBuffer();
  };

  $('btnNewStep').onclick = () => {
    const selector = $('pickSelector').value.trim();
    if (!selector) return toast('Пустой селектор', 'err');
    insertIntoTarget($('targetSection').value,
      buildStep(selector, $('pickAction').value, $('pickValue').value), $('insertMode').value);
  };

  $('btnToStep').onclick = () => {
    const selector = $('pickSelector').value.trim();
    if (!selector) return toast('Пустой селектор', 'err');
    const [id] = String($('targetSection').value || '').split('::');
    const target = getTarget(id);
    const step = target && target.currentStep();
    if (!step) return toast('Выберите шаг в списке — туда вставим селектор', 'err');
    step.action = $('pickAction').value;
    const previous = (step.selectors || []).filter((item) => item && item !== selector);
    delete step.selector;
    step.selectors = [selector].concat(previous);
    const value = $('pickValue').value;
    if (value && ['fill', 'type', 'select'].includes(step.action)) step.value = value;
    target.markDirty();
    target.render();
    toast('Селектор вставлен в выбранный шаг', 'ok');
  };
}
