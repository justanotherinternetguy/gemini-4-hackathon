(function () {
  var root = document.documentElement;
  // theme
  try { var t = localStorage.getItem('theme'); if (t) root.dataset.theme = t; } catch (e) {}
  var tb = document.querySelector('.themebtn');
  if (tb) tb.onclick = function () {
    var dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {}
  };
  // pill switches
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.switch button');
    if (!b) return;
    var g = b.dataset.group, id = b.dataset.id;
    b.parentNode.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    document.querySelectorAll('.pane[data-group="' + g + '"]').forEach(function (p) {
      p.classList.toggle('on', p.dataset.id === id);
      if (p.dataset.id !== id) p.querySelectorAll('video').forEach(function (v) { v.pause(); });
    });
  });
  // feed filters
  document.addEventListener('click', function (e) {
    var b = e.target.closest('.feedbar button');
    if (!b) return;
    b.classList.toggle('off');
    var bar = b.parentNode, feed = bar.nextElementSibling;
    var off = {}; bar.querySelectorAll('button.off').forEach(function (x) { off[x.dataset.filter] = 1; });
    feed.querySelectorAll('.step').forEach(function (s) { s.classList.toggle('hide', !!off[s.dataset.agent]); });
  });
  // chart tooltips
  var tip = document.createElement('div'); tip.className = 'tip'; document.body.appendChild(tip);
  document.addEventListener('mouseover', function (e) {
    var t = e.target.closest('[data-tip]'); if (!t) return;
    tip.textContent = t.getAttribute('data-tip'); tip.classList.add('on');
  });
  document.addEventListener('mousemove', function (e) {
    if (!tip.classList.contains('on')) return;
    var x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + 'px'; tip.style.top = (e.clientY + 16) + 'px';
  });
  document.addEventListener('mouseout', function (e) { if (e.target.closest('[data-tip]')) tip.classList.remove('on'); });
  // lightbox
  var lb = document.createElement('div'); lb.className = 'lb'; lb.innerHTML = '<div><img alt=""><p></p></div>';
  document.body.appendChild(lb);
  lb.onclick = function () { lb.classList.remove('on'); };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') lb.classList.remove('on'); });
  document.addEventListener('click', function (e) {
    var f = e.target.closest('[data-zoom]'); if (!f) return;
    var img = f.querySelector('img'); if (!img) return;
    lb.querySelector('img').src = img.src;
    var c = f.querySelector('figcaption'); lb.querySelector('p').textContent = c ? c.textContent : '';
    lb.classList.add('on');
  });
  // section highlight in the top bar
  var links = {}; document.querySelectorAll('.tabs a').forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });
  var owner = {}; document.querySelectorAll('section[id]').forEach(function (s) {
    var id = s.id, top = id.split('-')[0]; owner[id] = links[id] ? id : (links[top] ? top : null);
  });
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        var o = owner[en.target.id]; if (!o) return;
        Object.keys(links).forEach(function (k) { links[k].classList.toggle('on', k === o); });
      });
    }, { rootMargin: '-40% 0px -55% 0px' });
    document.querySelectorAll('section[id]').forEach(function (s) { io.observe(s); });
    var io2 = new IntersectionObserver(function (es) {
      es.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('inview'); io2.unobserve(en.target); } });
    }, { threshold: .3 });
    document.querySelectorAll('[data-inview]').forEach(function (s) { io2.observe(s); });
  } else document.querySelectorAll('[data-inview]').forEach(function (s) { s.classList.add('inview'); });
  // live tools: embed when the local server answers
  document.querySelectorAll('.live[data-live]').forEach(function (f) {
    var url = f.dataset.live;
    fetch(url, { mode: 'no-cors', cache: 'no-store' }).then(function () {
      f.innerHTML = '<iframe src="' + url + '" title="live tool" allow="camera; microphone; clipboard-write; fullscreen" style="width:100%;height:min(80vh,860px);border:0;display:block;background:#fff" loading="lazy"></iframe>';
    }).catch(function () {});
  });
})();
