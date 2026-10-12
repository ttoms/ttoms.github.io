// 主题切换 + 博客分类筛选
(function () {
  // 主题切换
  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var html = document.documentElement;
      var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
  }

  // 博客分类筛选
  var filter = document.getElementById('category-filter');
  var list = document.getElementById('post-list');
  if (filter && list) {
    filter.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-cat]');
      if (!btn) return;
      filter.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      var cat = btn.dataset.cat;
      list.querySelectorAll('.post-item').forEach(function (item) {
        item.style.display = (cat === '*' || item.dataset.category === cat) ? '' : 'none';
      });
    });
  }

  // 知识卡片目录：展开状态跨页持久化
  var EXPAND_KEY = 'cards-sidebar-open';
  function readOpen() {
    try { return new Set(JSON.parse(localStorage.getItem(EXPAND_KEY) || '[]')); }
    catch (e) { return new Set(); }
  }
  var openSet = readOpen();
  document.querySelectorAll('.sidebar details[data-path]').forEach(function (d) {
    if (openSet.has(d.dataset.path)) d.open = true;
    d.addEventListener('toggle', function () {
      if (d.open) openSet.add(d.dataset.path); else openSet.delete(d.dataset.path);
      localStorage.setItem(EXPAND_KEY, JSON.stringify(Array.from(openSet)));
    });
  });

  // 目录宽度拖拽调整 + 持久化
  var resizer = document.getElementById('sidebar-resizer');
  var sidebarEl = document.querySelector('.sidebar');
  var layout = document.querySelector('.cards-layout');
  if (resizer && sidebarEl && layout) {
    var WIDTH_KEY = 'cards-sidebar-width';
    var saved = parseInt(localStorage.getItem(WIDTH_KEY), 10);
    if (saved >= 160 && saved <= 800) {
      document.documentElement.style.setProperty('--sidebar-w', saved + 'px');
    }
    var dragging = false;
    resizer.addEventListener('mousedown', function (e) {
      dragging = true;
      resizer.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var w = e.clientX - layout.getBoundingClientRect().left;
      w = Math.max(160, Math.min(800, w));
      document.documentElement.style.setProperty('--sidebar-w', w + 'px');
    });
    document.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      var w = parseInt(document.documentElement.style.getPropertyValue('--sidebar-w'), 10);
      if (w) localStorage.setItem(WIDTH_KEY, w);
    });
  }

  // 目录固定 / 自动隐藏
  var pinBtn = document.getElementById('pin-toggle');
  var sidebarBox = document.getElementById('sidebar');
  if (pinBtn && sidebarBox) {
    var PIN_KEY = 'cards-sidebar-pinned';
    function applyPin(pinned) {
      sidebarBox.classList.toggle('auto-hide', !pinned);
      pinBtn.textContent = pinned ? '📌' : '📍';
      pinBtn.title = pinned ? '固定目录（点击切换自动隐藏）' : '自动隐藏目录（点击固定）';
    }
    var pinned = localStorage.getItem(PIN_KEY) !== '0';  // 默认固定
    applyPin(pinned);
    pinBtn.addEventListener('click', function () {
      applyPin(sidebarBox.classList.contains('auto-hide'));
      localStorage.setItem(PIN_KEY, sidebarBox.classList.contains('auto-hide') ? '0' : '1');
    });
  }

  // 内容宽度拖拽（卡片详情页 + 博客文章页）
  var cResizer = document.getElementById('content-resizer');
  var cTarget = document.querySelector('.card-page') || document.querySelector('.post');
  if (cResizer && cTarget) {
    var CW_KEY = 'content-width';
    var savedCW = parseInt(localStorage.getItem(CW_KEY), 10);
    if (savedCW >= 400 && savedCW <= 1600) {
      document.documentElement.style.setProperty('--content-w', savedCW + 'px');
    }
    var cDrag = false, cStartX = 0, cStartW = 0;
    cResizer.addEventListener('mousedown', function (e) {
      cDrag = true;
      cResizer.classList.add('dragging');
      cStartX = e.clientX;
      cStartW = cTarget.getBoundingClientRect().width;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!cDrag) return;
      var w = cStartW + (e.clientX - cStartX);
      w = Math.max(400, Math.min(1600, w));
      document.documentElement.style.setProperty('--content-w', w + 'px');
    });
    document.addEventListener('mouseup', function () {
      if (!cDrag) return;
      cDrag = false;
      cResizer.classList.remove('dragging');
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      var w = parseInt(document.documentElement.style.getPropertyValue('--content-w'), 10);
      if (w) localStorage.setItem(CW_KEY, w);
    });
  }
})();
