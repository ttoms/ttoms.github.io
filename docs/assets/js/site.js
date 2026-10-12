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
})();
