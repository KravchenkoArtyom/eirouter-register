/* Раздел «Аккаунты»: что уже зарегистрировано. */
'use strict';

import { $, api, esc, toast } from './util.js';

export async function loadAccounts() {
  try {
    const data = await api('/api/accounts');
    if (!data.files.length) {
      $('accounts').innerHTML = '<p class="hint">Файлов аккаунтов пока нет: '
        + 'первый успешный запуск создаст universal_accounts.json.</p>';
      return data;
    }
    $('accounts').innerHTML = data.files.map((file) => `<h3>${esc(file.file)} — ${file.total}</h3>
      <table><tr><th>Почта</th><th>Сценарий</th><th>Статус</th><th>Ключ</th></tr>
      ${file.accounts.map((account) => {
        const kind = account.status === 'active' ? 'active'
          : (account.status || '').includes('pending') ? 'pending' : '';
        return `<tr><td class="mono">${esc(account.email)}</td><td>${esc(account.provider)}</td>
          <td><span class="pill ${kind}">${esc(account.status)}</span></td>
          <td>${account.has_key ? 'да' : '—'}</td></tr>`;
      }).join('')}</table>`).join('');
    return data;
  } catch (error) {
    toast(error.message, 'err');
    return { files: [] };
  }
}

export function initAccounts() {
  $('btnAccounts').onclick = loadAccounts;
}
