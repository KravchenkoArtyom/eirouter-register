/* Мелочи, нужные всем разделам: DOM, запросы, сообщения, звук. */
'use strict';

export const $ = (id) => document.getElementById(id);

export function esc(value) {
  return String(value === undefined || value === null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function toast(message, kind) {
  const node = document.createElement('div');
  node.className = 'toast ' + (kind || '');
  node.textContent = message;
  $('toasts').appendChild(node);
  setTimeout(() => node.remove(), 5000);
}

export async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = '';
    try {
      const data = await response.json();
      detail = data.detail || JSON.stringify(data);
    } catch (error) {
      detail = response.statusText;
    }
    throw new Error(detail || ('HTTP ' + response.status));
  }
  return response.status === 204 ? null : response.json();
}

export function sendJson(url, method, body) {
  return api(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
}

export function number(value, fallback) {
  const parsed = Number(String(value).trim());
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function lines(text) {
  return String(text || '').split('\n').map((line) => line.trim()).filter(Boolean);
}

/* Звук уведомления делаем сами: файл не нужен, а один короткий тон
   слышно и в свёрнутом окне. */
export function beep() {
  try {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;
    const audio = new Ctor();
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = 660;
    gain.gain.setValueAtTime(0.0001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.2, audio.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.5);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start();
    oscillator.stop(audio.currentTime + 0.55);
    setTimeout(() => audio.close().catch(() => {}), 900);
  } catch (error) { /* звук — не повод падать */ }
}

export function desktopNotify(title, body) {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    try { new Notification(title, { body, tag: 'registrar' }); } catch (error) { /* ok */ }
  } else if (Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
}

export function humanSeconds(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return value + ' с';
  return Math.floor(value / 60) + ' мин ' + String(value % 60).padStart(2, '0') + ' с';
}
