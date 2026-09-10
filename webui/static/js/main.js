/* Точка входа панели: справочники, статус окружения, сборка разделов. */
'use strict';

import { $, api, toast } from './util.js';
import { state } from './state.js';
import { initTheme } from './theme.js';
import { initNav, onView, showView } from './nav.js';
import { initScenarios, loadScenario, refreshScenarios, saveScenario } from './scenario.js';
import { initMail, refreshMailServices, saveMailService } from './mail.js';
import { initProxies, refreshProxies } from './proxies.js';
import { initRuns, loadSettings } from './runs.js';
import { initInspector, loadBuffer, openInspector, refreshTargets } from './inspector.js';
import { initAccounts, loadAccounts } from './accounts.js';
import { initOverview, renderChecklist } from './overview.js';

async function refreshEnvironment() {
  const [environment, accounts] = await Promise.all([
    api('/api/environment').catch(() => ({})),
    api('/api/accounts').catch(() => ({ files: [] }))
  ]);
  state.environment = environment;
  const total = (accounts.files || []).reduce((sum, file) => sum + file.total, 0);
  $('envStatus').innerHTML = [
    `Сценариев: <b>${state.scenarios.length}</b>`,
    `Аккаунтов: <b>${total}</b>`,
    `Прокси: <b>${environment.proxies || 0}</b>`,
    `Свои почты: <b>${environment.mail_services || 0}</b>`,
    `AdsPower: <b>${environment.adspower ? 'есть' : 'нет'}</b>`
  ].join('');
  return environment;
}

async function boot() {
  initTheme();
  try {
    state.catalog = await api('/api/actions');
    await Promise.all([refreshMailServices(), loadSettings()]);
    initScenarios();
    initMail(openInspector);
    const runs = initRuns({ onEnvironmentChange: async () => {
      await refreshEnvironment();
      renderChecklist();
      loadAccounts();
    } });
    initProxies(async () => {
      await refreshEnvironment();
      runs.renderProxyMode();
      renderChecklist();
    });
    initInspector();
    initAccounts();
    initOverview(refreshEnvironment);
    refreshTargets();

    await Promise.all([refreshScenarios(), refreshProxies(), loadBuffer()]);
    await refreshEnvironment();
    runs.renderProxyMode();
    renderChecklist();
    $('health').className = 'status-dot ok';
    if ($('edScenario').value) await loadScenario($('edScenario').value);
  } catch (error) {
    $('health').className = 'status-dot bad';
    toast('Сервер недоступен: ' + error.message, 'err');
    return;
  }

  const templates = await api('/api/templates').catch(() => []);
  $('newTemplate').innerHTML = templates
    .map((item) => `<option value="${item.id}">${item.label}</option>`).join('');
  const mailTemplates = await api('/api/mail/templates').catch(() => []);
  $('mailNewTemplate').innerHTML = mailTemplates
    .map((item) => `<option value="${item.id}">${item.label}</option>`).join('');

  onView('accounts', loadAccounts);
  onView('proxy', () => refreshProxies().catch(() => {}));
  onView('overview', renderChecklist);
  initNav();

  // Ctrl+S сохраняет то, что открыто: сценарий или почтовый сервис.
  document.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
    event.preventDefault();
    const active = document.querySelector('main .view.on');
    const view = active ? active.dataset.view : '';
    if (view === 'mail') saveMailService();
    else saveScenario();
  });
}

boot();
