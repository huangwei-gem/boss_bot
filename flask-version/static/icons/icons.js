/* 本地图标：把 <i data-ki="名字"> 换成 ui-sprite.svg 里的同名 <symbol>。
 *
 * 这里原先挂的是 https://unpkg.com/lucide@latest —— CDN 不通或者断网时整屏图标一起消失，
 * 版本号还一直漂。现在图标随面板一起发，雪碧图一次请求，颜色走 currentColor
 * 所以按钮的 hover/禁用/暗色主题不用各存一套图。
 */
(function () {
  var SPRITE = '/static/icons/ui-sprite.svg';
  var NS = 'http://www.w3.org/2000/svg';

  function createIcons(root) {
    var nodes = (root || document).querySelectorAll('[data-ki]');
    for (var i = nodes.length - 1; i >= 0; i--) {
      var el = nodes[i];
      var svg = document.createElementNS(NS, 'svg');
      var cls = 'ki' + (el.getAttribute('class') ? ' ' + el.getAttribute('class') : '');
      svg.setAttribute('class', cls);
      // 图标都是文字的配图，读屏应该念文字而不是念图
      svg.setAttribute('aria-hidden', 'true');
      if (el.getAttribute('style')) svg.setAttribute('style', el.getAttribute('style'));
      var use = document.createElementNS(NS, 'use');
      use.setAttribute('href', SPRITE + '#i-' + el.getAttribute('data-ki'));
      svg.appendChild(use);
      el.parentNode.replaceChild(svg, el);
    }
  }

  window.KIcon = { createIcons: createIcons };
})();
