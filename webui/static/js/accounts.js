/* Раздел «Аккаунты»: что уже зарегистрировано. */
'use strict';

import { $, api, esc, toast } from './util.js';

/* Последний ответ панели — чтобы фильтровать без нового запроса. */
let loaded = { files: [] };

/* Подходит ли запись под строку поиска (почта, сценарий, статус). */
function matches(account, query) {
  if (!query) return true;
  const haystack = [account.email, account.provider, account.status]
    .map((value) => String(value || '').toLowerCase()).join(' ');
  return haystack.includes(query);
}

function render() {
  const query = ($('accountFilter').value || '').trim().toLowerCase();
  const files = loaded.files || [];
  if (!files.length) {
    $('accounts').innerHTML = '<p class="hint">Файлов аккаунтов пока нет: '
      + 'первый успешный запуск создаст universal_accounts.json.</p>';
    return;
  }
  const blocks = files.map((file) => {
    const rows = (file.accounts || []).filter((account) => matches(account, query));
    const title = query
      ? `${esc(file.file)} — ${rows.length} из ${file.total}`
      : `${esc(file.file)} — ${file.total}`;
    if (!rows.length) {
      return `<h3>${title}</h3><p class="hint">Под запрос ничего не подошло.</p>`;
    }
    return `<h3>${title}</h3>
      <table><tr><th>Почта</th><th>Сценарий</th><th>Статус</th><th>Ключ</th></tr>
      ${rows.map((account) => {
        const kind = account.status === 'active' ? 'active'
          : (account.status || '').includes('pending') ? 'pending' : '';
        return `<tr><td class="mono">${esc(account.email)}</td><td>${esc(account.provider)}</td>
          <td><span class="pill ${kind}">${esc(account.status)}</span></td>
          <td>${account.has_key ? 'да' : '—'}</td></tr>`;
      }).join('')}</table>`;
  });
  $('accounts').innerHTML = blocks.join('');
}

export async function loadAccounts() {
  try {
    loaded = await api('/api/accounts');
    render();
    return loaded;
  } catch (error) {
    toast(error.message, 'err');
    return { files: [] };
  }
}

export function initAccounts() {
  $('btnAccounts').onclick = loadAccounts;
  $('accountFilter').addEventListener('input', render);
}
