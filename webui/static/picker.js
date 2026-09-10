/* Сборщик элементов внутри зеркала страницы.
 *
 * Скрипт дописывается сервером в зеркало (webui/inspect_session.py) и работает
 * в том же документе, что и разметка сайта: наведение подсвечивает элемент,
 * клик отдаёт родительской панели селекторы-кандидаты, Shift+клик просит
 * сразу положить элемент в буфер. Навигация и отправка форм заблокированы —
 * зеркало служит только для выбора элементов.
 */
(function () {
  'use strict';
  if (window.__webuiPicker) return;
  window.__webuiPicker = true;

  var IGNORE = ['__wp_box', '__wp_label'];
  var current = null;
  var frozen = null;

  var style = document.createElement('style');
  style.textContent = [
    '#__wp_box{position:fixed;pointer-events:none;z-index:2147483646;',
    'border:1px solid #4a4a4f;background:rgba(120,120,130,.14);border-radius:2px}',
    '#__wp_box.frozen{border:2px solid #1d1d20;background:rgba(120,120,130,.20)}',
    '#__wp_label{position:fixed;pointer-events:none;z-index:2147483647;background:#2b2b2f;',
    'color:#fff;font:11px/1.5 ui-monospace,Consolas,monospace;padding:2px 6px;border-radius:4px;',
    'max-width:60vw;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}'
  ].join('');
  (document.head || document.documentElement).appendChild(style);

  var box = document.createElement('div');
  box.id = '__wp_box';
  var label = document.createElement('div');
  label.id = '__wp_label';
  var host = document.body || document.documentElement;
  host.appendChild(box);
  host.appendChild(label);

  function send(type, payload) {
    try {
      parent.postMessage({ source: 'webui-picker', type: type, payload: payload }, location.origin);
    } catch (error) { /* панель закрыта */ }
  }

  function isOwn(node) {
    return !node || IGNORE.indexOf(node.id) >= 0;
  }

  function count(selector) {
    try { return document.querySelectorAll(selector).length; } catch (error) { return -1; }
  }

  function stableClasses(element) {
    return Array.prototype.filter.call(element.classList || [], function (name) {
      return name.length > 1 && !/\d{3,}/.test(name) && !/^(css|sc|jsx|emotion)-/.test(name);
    });
  }

  function cssPath(element) {
    var parts = [];
    var node = element;
    while (node && node.nodeType === 1 && parts.length < 6) {
      var name = node.tagName.toLowerCase();
      var parent = node.parentElement;
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (child) {
          return child.tagName === node.tagName;
        });
        if (same.length > 1) name += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(name);
      node = parent;
    }
    return parts.join(' > ');
  }

  function candidates(element) {
    var tag = element.tagName.toLowerCase();
    var list = [];
    function push(selector) {
      if (selector && list.indexOf(selector) < 0) list.push(selector);
    }
    if (element.id && !/\d{4,}/.test(element.id)) push('#' + CSS.escape(element.id));
    ['data-testid', 'data-test', 'data-qa', 'data-cy', 'name', 'aria-label', 'placeholder', 'autocomplete']
      .forEach(function (attribute) {
        var value = element.getAttribute && element.getAttribute(attribute);
        if (value && value.length < 60) {
          push(tag + '[' + attribute + '="' + value.replace(/"/g, '') + '"]');
        }
      });
    var type = (element.getAttribute && element.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type) push('input[type=' + type + ']');
    var text = (element.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    if (text && /^(button|a|label)$/.test(tag)) {
      push(tag + ':has-text("' + text.replace(/"/g, '') + '")');
    }
    var classes = stableClasses(element);
    if (classes.length) {
      push(tag + '.' + classes.slice(0, 2).map(function (name) { return CSS.escape(name); }).join('.'));
    }
    push(cssPath(element));
    return list.map(function (selector) {
      var found = count(selector);
      return {
        sel: selector,
        count: found,
        unique: found === 1,
        // :has-text — селектор Playwright, в браузере не считается: проверяем на сервере
        playwright: selector.indexOf(':has-text(') >= 0
      };
    });
  }

  function suggestAction(element) {
    var tag = element.tagName.toLowerCase();
    var type = (element.getAttribute && element.getAttribute('type') || '').toLowerCase();
    if (tag === 'select') return 'select';
    if (type === 'checkbox' || type === 'radio') return 'check';
    if (tag === 'textarea') return 'fill';
    if (tag === 'input' && ['submit', 'button', 'image', 'reset'].indexOf(type) < 0) return 'fill';
    return 'click';
  }

  function suggestValue(element) {
    var attributes = ['name', 'id', 'placeholder', 'autocomplete', 'type']
      .map(function (name) { return element.getAttribute && element.getAttribute(name) || ''; })
      .join(' ').toLowerCase();
    if (suggestAction(element) !== 'fill') return '';
    if (/e-?mail/.test(attributes)) return '{email}';
    if (/confirm|repeat|again|retype|password2|pwd2/.test(attributes)) return '{password_confirm}';
    if (/pass|pwd/.test(attributes)) return '{password}';
    if (/code|otp|pin/.test(attributes)) return '{code}';
    if (/user|login|nick|account|name/.test(attributes)) return '{login}';
    return '';
  }

  function describe(element) {
    return {
      tag: element.tagName.toLowerCase(),
      text: (element.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60),
      path: cssPath(element),
      url: document.baseURI,
      candidates: candidates(element),
      suggested: suggestAction(element),
      value: suggestValue(element)
    };
  }

  function outline(element, isFrozen) {
    if (!element) {
      box.style.display = 'none';
      label.style.display = 'none';
      return;
    }
    var rect = element.getBoundingClientRect();
    box.style.display = 'block';
    box.className = isFrozen ? 'frozen' : '';
    box.style.left = rect.left + 'px';
    box.style.top = rect.top + 'px';
    box.style.width = rect.width + 'px';
    box.style.height = rect.height + 'px';
    var classes = stableClasses(element).slice(0, 2).map(function (name) { return '.' + name; }).join('');
    label.textContent = element.tagName.toLowerCase() + (element.id ? '#' + element.id : '') + classes
      + '  ' + Math.round(rect.width) + '×' + Math.round(rect.height);
    label.style.display = 'block';
    var top = rect.top > 20 ? rect.top - 19 : rect.bottom + 3;
    label.style.left = Math.max(2, rect.left) + 'px';
    label.style.top = top + 'px';
  }

  document.addEventListener('mousemove', function (event) {
    var element = event.target;
    if (isOwn(element) || !element || element.nodeType !== 1) return;
    current = element;
    outline(element, false);
    send('hover', { path: cssPath(element), tag: element.tagName.toLowerCase() });
  }, true);

  document.addEventListener('click', function (event) {
    var element = event.target;
    event.preventDefault();
    event.stopPropagation();
    if (isOwn(element) || !element || element.nodeType !== 1) return;
    frozen = element;
    outline(element, true);
    send(event.shiftKey ? 'save' : 'pick', describe(element));
  }, true);

  // Зеркало не должно никуда уходить: гасим всё, что вызывает переход.
  ['mousedown', 'mouseup', 'auxclick', 'dblclick', 'submit'].forEach(function (name) {
    document.addEventListener(name, function (event) {
      event.preventDefault();
      event.stopPropagation();
    }, true);
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      frozen = null;
      outline(null);
      send('clear', {});
    }
  }, true);

  window.addEventListener('scroll', function () { outline(frozen || current, !!frozen); }, true);
  window.addEventListener('resize', function () { outline(frozen || current, !!frozen); });

  window.addEventListener('message', function (event) {
    if (event.origin !== location.origin || !event.data) return;
    var data = event.data;
    if (data.type === 'highlight') {
      var element = null;
      try { element = document.querySelector(data.selector); } catch (error) { element = null; }
      if (element && element.scrollIntoView) {
        element.scrollIntoView({ block: 'center', inline: 'center' });
      }
      frozen = element;
      outline(element, true);
      send('highlighted', { found: !!element, selector: data.selector });
    } else if (data.type === 'clear') {
      frozen = null;
      outline(null);
    }
  });

  outline(null);
  send('ready', { url: document.baseURI, title: document.title });
})();
