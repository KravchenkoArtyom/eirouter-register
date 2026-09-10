/* Раздел «Запуск»: очередь аккаунтов, лог, настройки задержек и капчи.
 *
 * Капча — единственное место, где программа зовёт человека немедленно:
 * сверху появляется полоса с кнопками, звучит сигнал и (если разрешено)
 * приходит системное уведомление.
 */
'use strict';

import { $, api, beep, desktopNotify, esc, humanSeconds, number, sendJson, toast } from './util.js';
import { state } from './state.js';

const SETTING_FIELDS = {
  delays: {
    step_min_ms: 'setStepMin', step_max_ms: 'setStepMax', click_extra_ms: 'setClickExtra',
    account_min_s: 'setAccountMin', account_max_s: 'setAccountMax',
    after_captcha_s: 'setAfterCaptcha'
  },
  captcha: {
    mode: 'setCaptchaMode', timeout: 'setCaptchaTimeout', pause_s: 'setCaptchaPause',
    selectors: 'setCaptchaSelectors', enabled: 'setCaptchaEnabled', check_text: 'setCaptchaText'
  },
  notify: { sound: 'setSound', desktop: 'setDesktop' }
};

let onEnvironmentChange = () => {};

/* Как называть состояние запуска человеческим языком. */
const STATUS_TEXT = {
  running: 'идёт',
  done: 'всё готово',
  done_with_errors: 'закончено, но не всё вышло',
  stopped: 'остановлено вами',
  failed: 'запуск сорвался'
};

/* ---------- настройки ---------- */
export async function loadSettings() {
  const data = await api('/api/settings');
  state.settings = data.settings;
  state.options = data.options;
  fillSettingsForm(data.settings);
  return data.settings;
}

function fillSettingsForm(settings) {
  Object.entries(SETTING_FIELDS.delays).forEach(([key, id]) => {
    $(id).value = settings.delays[key];
  });
  $('setCaptchaMode').value = settings.captcha.mode;
  $('setCaptchaTimeout').value = settings.captcha.timeout;
  $('setCaptchaPause').value = settings.captcha.pause_s;
  $('setCaptchaSelectors').value = (settings.captcha.selectors || []).join(', ');
  $('setCaptchaEnabled').checked = settings.captcha.enabled;
  $('setCaptchaText').checked = settings.captcha.check_text;
  $('setSound').checked = settings.notify.sound;
  $('setDesktop').checked = settings.notify.desktop;
  $('settingsState').textContent = 'Загружено.';
}

function settingsPayload() {
  return {
    delays: {
      step_min_ms: number($('setStepMin').value, 150),
      step_max_ms: number($('setStepMax').value, 600),
      click_extra_ms: number($('setClickExtra').value, 250),
      account_min_s: number($('setAccountMin').value, 0),
      account_max_s: number($('setAccountMax').value, 0),
      after_captcha_s: number($('setAfterCaptcha').value, 3)
    },
    captcha: {
      mode: $('setCaptchaMode').value,
      timeout: number($('setCaptchaTimeout').value, 300),
      pause_s: number($('setCaptchaPause').value, 20),
      selectors: $('setCaptchaSelectors').value,
      enabled: $('setCaptchaEnabled').checked,
      check_text: $('setCaptchaText').checked,
      notify: true
    },
    notify: {
      sound: $('setSound').checked,
      desktop: $('setDesktop').checked,
      toast: true
    }
  };
}

/* ---------- лог ---------- */
function logLine(text) {
  const node = document.createElement('div');
  const lowered = String(text).toLowerCase();
  if (lowered.includes('не удал') || lowered.includes('ошибка') || lowered.includes('fail')) {
    node.className = 'err';
  } else if (lowered.includes('капча') || lowered.includes('останов')
    || lowered.includes('пропущен') || lowered.includes('пауза')) {
    node.className = 'warn';
  } else if (lowered.includes('готово') || lowered.includes('сохранён')) {
    node.className = 'ok';
  }
  node.textContent = text;
  $('log').appendChild(node);
  if ($('autoscroll').checked) $('log').scrollTop = $('log').scrollHeight;
}

function renderNotices(notices) {
  $('notices').innerHTML = (notices || []).slice(-5).reverse().map((item) => {
    const time = new Date((item.at || 0) * 1000).toLocaleTimeString();
    return `<div class="notice ${esc(item.level || 'info')}">
      <span class="mono muted">${esc(time)}</span> ${esc(item.message || item.type)}</div>`;
  }).join('');
}

/* ---------- капча ---------- */
function renderCaptcha(job) {
  const bar = $('captchaBar');
  const captcha = job && job.captcha;
  if (!captcha) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  const waiting = Math.max(0, Math.round(Date.now() / 1000 - (captcha.at || 0)));
  $('captchaTitle').textContent = `Капча: ${captcha.kind || 'проверка'} — ждём вас ${humanSeconds(waiting)}`;
  $('captchaDetail').textContent = [captcha.where, captcha.selector, captcha.url]
    .filter(Boolean).join(' · ');
  if (captcha.seq && captcha.seq !== state.captchaSeq) {
    state.captchaSeq = captcha.seq;
    const settings = state.settings || { notify: {} };
    if (settings.notify.sound) beep();
    if (settings.notify.desktop) {
      desktopNotify('Капча в регистраторе', 'Решите проверку в окне браузера');
    }
    toast('Капча: решите проверку в окне браузера', 'err');
  }
}

async function answerCaptcha(action) {
  if (!state.job) return;
  try {
    await sendJson(`/api/runs/${state.job}/captcha`, 'POST', { action });
    $('captchaBar').hidden = true;
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* ---------- прокси в запуске ---------- */
function renderProxyState(job) {
  const items = (job && job.proxies) || [];
  if (!items.length) {
    $('runProxyState').innerHTML = '';
    return;
  }
  $('runProxyState').innerHTML = '<table><tr><th>Прокси</th><th>Успехов</th><th>Неудач</th>'
    + '<th>Состояние</th></tr>'
    + items.map((item) => `<tr><td class="mono">${esc(item.proxy)}</td><td>${item.ok}</td>
        <td>${item.fail}</td><td><span class="pill ${item.banned ? 'pending' : (item.current ? 'active' : '')}">
        ${item.banned ? 'выключен' : (item.current ? 'сейчас' : 'в очереди')}</span></td></tr>`).join('')
    + '</table>';
}

/* ---------- состояние запуска ---------- */

/* Отрисовать очередной снимок запуска: лог, прогресс, капча, прокси. */
function applyJob(job) {
  if (!job) return;
  if (typeof job.next_offset === 'number') state.logOffset = job.next_offset;
  if (job.dropped && job.dropped !== state.dropped) {
    state.dropped = job.dropped;
    logLine(`[webui] Ранние строки лога обрезаны (${job.dropped}); `
      + `полный лог — в logs/runs/${job.log_file || ''}`);
  }
  (job.logs || []).forEach(logLine);
  const done = job.done + job.failed + job.skipped;
  $('runProgressBar').style.width = Math.round(done / Math.max(1, job.count) * 100) + '%';
  const pause = job.pause_left ? `, пауза ${job.pause_left} с` : '';
  const status = STATUS_TEXT[job.status] || job.status;
  const workers = job.workers > 1 ? `, в ${job.workers} потока` : '';
  $('runStatus').textContent = `${job.scenario}: готово ${job.done}, неудач ${job.failed}, `
    + `пропущено ${job.skipped} из ${job.count}${workers} — ${status}${pause}`;
  renderCaptcha(job);
  renderNotices(job.notices);
  renderProxyState(job);
  if (job.status !== 'running') finishRun();
}

/* Запуск закончился: вернуть кнопки в исходное состояние и закрыть подписки. */
function finishRun() {
  stopWatch();
  $('btnStart').disabled = false;
  $('btnStop').disabled = true;
  $('captchaBar').hidden = true;
  onEnvironmentChange();
}

/* Отключить и поток событий, и опрос по таймеру. */
function stopWatch() {
  if (state.stream) {
    state.stream.close();
    state.stream = null;
  }
  if (state.poll) {
    clearInterval(state.poll);
    state.poll = null;
  }
}

/* Опрос по таймеру — запас на случай, если поток событий не работает. */
async function pollRun() {
  if (!state.job) return;
  try {
    applyJob(await api('/api/runs/' + state.job + '?offset=' + state.logOffset));
  } catch (error) { /* сеть моргнула — попробуем на следующем такте */ }
}

function startPolling() {
  if (state.poll) return;
  state.poll = setInterval(pollRun, 1000);
  pollRun();
}

/* Основной способ следить за запуском: сервер сам присылает изменения. */
function watchRun() {
  stopWatch();
  if (typeof EventSource !== 'function') {
    startPolling();
    return;
  }
  let alive = false;
  const stream = new EventSource(`/api/runs/${state.job}/stream?offset=${state.logOffset}`);
  state.stream = stream;
  stream.onmessage = (event) => {
    alive = true;
    try {
      applyJob(JSON.parse(event.data));
    } catch (error) { /* мусор в кадре — подождём следующий */ }
  };
  stream.onerror = () => {
    stream.close();
    if (state.stream !== stream) return;
    state.stream = null;
    /* Поток мог закрыться и потому, что запуск закончился — сверимся опросом. */
    if (alive) pollRun();
    startPolling();
  };
  /* Если поток так и не заговорил, через пару секунд берём опрос. */
  setTimeout(() => { if (!alive && state.stream === stream) startPolling(); }, 3000);
}

async function startRun() {
  const mode = $('runProxy').value;
  const body = {
    scenario: $('runScenario').value,
    count: parseInt($('runCount').value || '1', 10),
    mail: $('runMail').value,
    proxy_mode: mode,
    browser: $('runBrowser').value,
    rotation: $('runRotation').value,
    proxy: $('runProxyValue').value.trim(),
    workers: number($('runWorkers').value, 1),
    delays: settingsPayload().delays,
    captcha: settingsPayload().captcha
  };
  try {
    const result = await sendJson('/api/runs', 'POST', body);
    state.job = result.id;
    state.logOffset = 0;
    state.captchaSeq = 0;
    state.dropped = 0;
    $('log').innerHTML = '';
    $('notices').innerHTML = '';
    $('btnStart').disabled = true;
    $('btnStop').disabled = false;
    logLine('[webui] Запуск ' + state.job);
    watchRun();
  } catch (error) {
    toast(error.message, 'err');
  }
}

function renderProxyMode() {
  const mode = $('runProxy').value;
  $('runProxyOne').hidden = mode !== 'single';
  $('runRotationWrap').hidden = mode === 'direct' || mode === 'single';
  const count = (state.environment && state.environment.proxies) || 0;
  $('runProxyHint').textContent = mode === 'file'
    ? `В файле ${state.environment.proxies_file || 'proxies.txt'}: ${count} адресов. `
      + 'Пополнить и проверить — в разделе «Прокси».'
    : (mode === 'single' ? 'Один адрес на все аккаунты этого запуска.'
      : 'Соединения пойдут напрямую, без прокси.');
}

export function initRuns(hooks) {
  onEnvironmentChange = (hooks && hooks.onEnvironmentChange) || (() => {});

  $('btnStart').onclick = startRun;
  $('btnStop').onclick = async () => {
    if (!state.job) return;
    await api('/api/runs/' + state.job + '/stop', { method: 'POST' }).catch(() => {});
    toast('Остановлюсь после текущего аккаунта');
  };
  $('btnClearLog').onclick = () => { $('log').innerHTML = ''; };
  $('runProxy').addEventListener('change', renderProxyMode);

  $('btnCaptchaDone').onclick = () => answerCaptcha('resolved');
  $('btnCaptchaSkip').onclick = () => answerCaptcha('skip');
  $('btnCaptchaStop').onclick = () => answerCaptcha('stop');

  $('btnSaveSettings').onclick = async () => {
    try {
      const saved = await sendJson('/api/settings', 'PUT', settingsPayload());
      state.settings = saved.settings;
      fillSettingsForm(saved.settings);
      $('settingsState').textContent = 'Сохранено.';
      toast('Настройки сохранены', 'ok');
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  $('btnResetSettings').onclick = async () => {
    try {
      const data = await api('/api/settings');
      fillSettingsForm(data.defaults);
      $('settingsState').textContent = 'Значения по умолчанию подставлены — нажмите «Сохранить».';
    } catch (error) {
      toast(error.message, 'err');
    }
  };

  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey && event.key === 'Enter' && !$('btnStart').disabled) startRun();
  });

  renderProxyMode();
  return { renderProxyMode };
}
