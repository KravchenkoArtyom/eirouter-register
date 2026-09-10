/* Раздел «Прокси»: список, файл, ротация и проверка связи. */
'use strict';

import { $, api, esc, sendJson, toast } from './util.js';
import { state } from './state.js';

let rotations = [];

export async function refreshProxies() {
  // text=1 — сервер отдаёт сам список с логинами только редактору.
  const data = await api('/api/proxies?text=1');
  rotations = data.rotations || [];
  $('proxyText').value = data.text || '';
  $('proxyFile').textContent = data.exists
    ? `Файл ${data.file}: ${data.count} адресов`
    : `Файл ${data.file} ещё не создан`;
  renderState(data);
  fillRotationSelects();
  return data;
}

function renderState(data) {
  const parts = [`адресов: ${data.count}`];
  if (data.errors && data.errors.length) parts.push('ошибок: ' + data.errors.length);
  $('proxyState').textContent = parts.join(', ');
  $('proxyResults').innerHTML = (data.errors || []).length
    ? `<p class="hint err-text">${data.errors.map(esc).join('<br>')}</p>` : '';
}

function fillRotationSelects() {
  const html = rotations
    .map((item) => `<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  const saved = (state.settings && state.settings.proxy) || {};
  ['proxyRotation', 'runRotation'].forEach((id) => {
    const keep = $(id).value;
    $(id).innerHTML = html;
    $(id).value = keep || saved.mode || 'per_account';
  });
  $('proxyBanAfter').value = saved.ban_after === undefined ? 2 : saved.ban_after;
}

async function saveText(text, ignoreErrors) {
  const result = await sendJson('/api/proxies', 'PUT', { text, ignore_errors: ignoreErrors });
  await refreshProxies();
  toast(`Сохранено адресов: ${result.count}`
    + (result.skipped.length ? `, пропущено строк: ${result.skipped.length}` : ''), 'ok');
}

export function initProxies(onSaved) {
  $('btnProxySave').onclick = async () => {
    try {
      await saveText($('proxyText').value, false);
      if (onSaved) onSaved();
    } catch (error) {
      if (!confirm(error.message + '\n\nСохранить только корректные строки?')) return;
      try {
        await saveText($('proxyText').value, true);
        if (onSaved) onSaved();
      } catch (nested) {
        toast(nested.message, 'err');
      }
    }
  };

  $('btnProxyReload').onclick = () => refreshProxies().catch((error) => toast(error.message, 'err'));

  $('btnProxyClear').onclick = async () => {
    if (!confirm('Очистить список прокси?')) return;
    try {
      await api('/api/proxies', { method: 'DELETE' });
      await refreshProxies();
      if (onSaved) onSaved();
      toast('Список очищен', 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('btnProxyCheck').onclick = async () => {
    const button = $('btnProxyCheck');
    button.disabled = true;
    $('proxyResults').innerHTML = '<p class="hint">Проверяю…</p>';
    try {
      const result = await sendJson('/api/proxies/check', 'POST', { text: $('proxyText').value });
      $('proxyResults').innerHTML = `<table><tr><th>Прокси</th><th>Итог</th><th>Внешний IP</th></tr>`
        + result.results.map((item) => `<tr><td class="mono">${esc(item.proxy)}</td>
            <td><span class="pill ${item.ok ? 'active' : 'pending'}">${item.ok ? item.ms + ' мс' : 'нет связи'}</span></td>
            <td class="mono">${esc(item.ok ? item.ip : (item.error || ''))}</td></tr>`).join('')
        + '</table>';
      toast(`Отвечают ${result.ok} из ${result.checked}`, result.ok ? 'ok' : 'err');
    } catch (error) {
      $('proxyResults').innerHTML = '';
      toast(error.message, 'err');
    } finally {
      button.disabled = false;
    }
  };

  $('btnProxySaveRotation').onclick = async () => {
    try {
      const saved = await sendJson('/api/settings', 'PUT', {
        proxy: { mode: $('proxyRotation').value, ban_after: Number($('proxyBanAfter').value || 0) }
      });
      state.settings = saved.settings;
      $('runRotation').value = saved.settings.proxy.mode;
      toast('Правило ротации запомнено', 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  /* Файл читает сам браузер: серверу уходит уже текст, поэтому загрузка
     работает и с перетаскиванием, и через выбор файла. */
  const readFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      $('proxyText').value = $('proxyText').value.trim()
        ? $('proxyText').value.replace(/\s*$/, '\n') + text
        : text;
      toast('Файл прочитан: проверьте список и нажмите «Сохранить»', 'ok');
    };
    reader.readAsText(file);
  };

  $('proxyFileInput').addEventListener('change', (event) => readFile(event.target.files[0]));
  const drop = $('proxyDrop');
  ['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (event) => {
    event.preventDefault();
    drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((type) => drop.addEventListener(type, () => drop.classList.remove('over')));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    readFile(event.dataTransfer.files[0]);
  });
}
