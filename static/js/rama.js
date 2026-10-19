/*
  Klikalna rama na stronie głównej (tło 4). Obrazy w stosie to ta sama scena z innym plakatem
  (składa je scripts/ramy.py). Kliknięcie ramy albo gwiazdy odsłania następny obraz kołem od
  miejsca kliknięcia, z iskrami i błyskiem na szybie. Ostatni obraz to pusta rama: wtedy
  rama prowadzi do wgrania pliku. Położenie ramy na ekranie liczymy z czworokąta w pikselach
  obrazu (data-frame) i dopasowania object-fit: cover.
*/
(function () {
  var scene = document.querySelector('[data-scena]');
  if (!scene) return;

  var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var canAnimate = !reducedMotion && typeof scene.animate === 'function';
  var images = Array.prototype.slice.call(scene.querySelectorAll('.scena__obraz'));
  var frame = JSON.parse(scene.getAttribute('data-frame'));
  var natW = parseFloat(scene.getAttribute('data-width'));
  var natH = parseFloat(scene.getAttribute('data-height'));
  var hotspot = scene.querySelector('[data-scena-rama]');
  var star = scene.querySelector('[data-scena-gwiazda]');
  var counter = scene.querySelector('[data-scena-licznik]');
  var caption = scene.querySelector('[data-scena-podpis]');
  var more = scene.querySelector('[data-scena-wiecej]');
  var switches = 0; // po drugiej zmianie obrazu pokazujemy link do galerii inspiracji
  var emptyLabel = scene.querySelector('[data-scena-pusta]');
  var flash = scene.querySelector('.scena__blysk');
  var fileInput = document.querySelector('#upload-form input[type=file]');
  var tag = scene.querySelector('[data-scena-metka]');
  var card = document.querySelector('[data-scena-kartka]');
  var promptBox = card.querySelector('[data-scena-prompt]');
  var copyBtn = card.querySelector('[data-scena-kopiuj]');
  var closeBtn = card.querySelector('[data-scena-zamknij]');
  var promptSuffix = scene.getAttribute('data-prompt-suffix') || '';
  var copiedUrl = scene.getAttribute('data-copied-url');
  var current = 0;
  var busy = false;
  var box = { x: 0, y: 0, w: 0, h: 0 };

  // Rama w pikselach ekranu: ten sam skalowanie i przesunięcie, jakie object-fit: cover
  // nadaje obrazowi (object-position w procentach).
  function layout() {
    var W = scene.clientWidth, H = scene.clientHeight;
    if (!W || !H) return;
    var pos = getComputedStyle(images[0]).objectPosition.split(' ');
    var px = parseFloat(pos[0]) / 100, py = parseFloat(pos[1] || '50%') / 100;
    var scale = Math.max(W / natW, H / natH);
    var ox = (W - natW * scale) * px, oy = (H - natH * scale) * py;
    var pts = frame.map(function (p) { return [p[0] * scale + ox, p[1] * scale + oy]; });
    var xs = pts.map(function (p) { return p[0]; }), ys = pts.map(function (p) { return p[1]; });
    box.x = Math.min.apply(null, xs); box.y = Math.min.apply(null, ys);
    box.w = Math.max.apply(null, xs) - box.x; box.h = Math.max.apply(null, ys) - box.y;
    // Kartka leży poza sceną (w hero), więc te same zmienne dostaje też rodzic
    [scene, scene.parentElement].forEach(function (el) {
      el.style.setProperty('--rama-x', box.x + 'px');
      el.style.setProperty('--rama-y', box.y + 'px');
      el.style.setProperty('--rama-w', box.w + 'px');
      el.style.setProperty('--rama-h', box.h + 'px');
    });
    var poly = 'polygon(' + pts.map(function (p) { return (p[0] - box.x) + 'px ' + (p[1] - box.y) + 'px'; }).join(', ') + ')';
    hotspot.style.clipPath = poly;
    flash.style.clipPath = poly;
    emptyLabel.style.clipPath = poly;
  }

  function isEmpty(i) { return images[i].getAttribute('data-empty') === '1'; }

  function update() {
    var empty = isEmpty(current);
    scene.classList.toggle('scena--pusta', empty);
    counter.hidden = empty;
    emptyLabel.hidden = !empty;
    tag.hidden = empty || !promptOf(current);
    var showMore = empty || switches >= 2;
    if (showMore && more.hidden) {
      more.hidden = false;
      if (canAnimate) {
        more.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: 700, delay: 900, easing: 'ease-out', fill: 'backwards' });
      }
    }
    hotspot.setAttribute('aria-label', empty ? 'Wgraj swój obrazek' : 'Pokaż następny plakat w ramie');
  }

  // Kartka z promptem: metka na ramie ją otwiera, zamyka X, Esc, klik poza nią albo zmiana obrazu.
  function promptOf(i) {
    var p = images[i].getAttribute('data-prompt');
    return p ? (p + ' ' + promptSuffix).trim() : '';
  }
  function openCard() {
    promptBox.textContent = promptOf(current);
    copyBtn.textContent = 'Kopiuj prompt';
    copyBtn.classList.remove('scena-kartka__kopiuj--ok');
    card.hidden = false;
    tag.setAttribute('aria-expanded', 'true');
    if (canAnimate) {
      card.animate([{ opacity: 0, transform: 'translateY(10px) rotate(-3deg)' }, { opacity: 1 }],
        { duration: 380, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1)' });
    }
    copyBtn.focus({ preventScroll: true });
  }
  function closeCard() {
    if (card.hidden) return;
    card.hidden = true;
    tag.setAttribute('aria-expanded', 'false');
  }
  function reportCopy() {
    if (!copiedUrl || !window.fetch) return;
    var body = new FormData();
    body.append('rama', images[current].getAttribute('data-file') || String(current));
    var token = document.querySelector('[name=csrfmiddlewaretoken]');
    if (token) body.append('csrfmiddlewaretoken', token.value);
    fetch(copiedUrl, { method: 'POST', body: body, credentials: 'same-origin', keepalive: true }).catch(function () {});
  }
  function copyPrompt() {
    var text = promptBox.textContent;
    var ok = function () {
      copyBtn.textContent = 'Skopiowane, do zobaczenia z plakatem';
      copyBtn.classList.add('scena-kartka__kopiuj--ok');
      reportCopy();
    };
    var fallback = function () {
      var range = document.createRange();
      range.selectNodeContents(promptBox);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try { document.execCommand('copy'); ok(); } catch (err) { copyBtn.textContent = 'Zaznacz tekst i skopiuj ręcznie'; }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok, fallback);
    else fallback();
  }
  tag.addEventListener('click', function () { if (card.hidden) openCard(); else closeCard(); });
  closeBtn.addEventListener('click', closeCard);
  copyBtn.addEventListener('click', copyPrompt);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeCard(); });
  document.addEventListener('click', function (e) {
    if (!card.hidden && !card.contains(e.target) && !tag.contains(e.target)) closeCard();
  });

  // Licznik i podpis zmieniają się w trakcie odsłaniania: stary podpis gaśnie, nowy wchodzi,
  // gdy obraz jest już w większości widoczny.
  function setCaption(i) {
    var img = images[i];
    var font = img.getAttribute('data-font');
    var text = img.getAttribute('data-caption') || '';
    var swap = function () {
      counter.textContent = (i + 1) + ' / ' + images.length;
      caption.textContent = text;
      caption.style.fontFamily = font ? '"' + font + '", var(--font-serif)' : '';
      caption.hidden = !text;
    };
    if (!canAnimate) { swap(); return; }
    var out = caption.animate([{ opacity: 1 }, { opacity: 0, transform: 'translateY(-6px)' }],
      { duration: 350, easing: 'ease-in', fill: 'forwards' });
    out.onfinish = function () {
      out.cancel();
      swap();
      caption.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
        { duration: 900, delay: 250, easing: 'ease-out', fill: 'backwards' });
    };
  }

  function sparks(x, y) {
    if (!canAnimate) return;
    for (var i = 0; i < 16; i++) {
      var s = document.createElement('span');
      s.className = 'scena__iskra';
      s.style.left = x + 'px';
      s.style.top = y + 'px';
      scene.appendChild(s);
      var a = Math.random() * Math.PI * 2, d = 50 + Math.random() * 110;
      var dx = Math.cos(a) * d, dy = Math.sin(a) * d - 20;
      s.animate([
        { transform: 'translate(-50%, -50%) scale(1)', opacity: 1 },
        { transform: 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px)) scale(0.1)', opacity: 0 }
      ], { duration: 1100 + Math.random() * 700, easing: 'cubic-bezier(0.1, 0.8, 0.3, 1)', fill: 'forwards' })
        .onfinish = function () { this.effect.target.remove(); };
    }
  }

  function glassFlash() {
    if (!canAnimate) return;
    flash.animate([
      { opacity: 0, transform: 'translateX(-70%) skewX(-18deg)' },
      { opacity: 0.85, offset: 0.35 },
      { opacity: 0, transform: 'translateX(70%) skewX(-18deg)' }
    ], { duration: 1300, easing: 'ease-in-out' });
  }

  // Odsłanianie nowego obrazu: miękkie maski (gradient w mask-image, animowane położenie i rozmiar
  // maski), każdy obraz ma swój wariant: krąg od kliknięcia, zasłony z różnych stron, rozmycie.
  var DURATION = 1900;
  var EASE = 'cubic-bezier(0.45, 0.05, 0.25, 1)';
  function softMask(img, gradient, size, from, to) {
    img.style.maskImage = gradient;
    img.style.webkitMaskImage = gradient;
    img.style.maskRepeat = img.style.webkitMaskRepeat = 'no-repeat';
    img.style.maskSize = img.style.webkitMaskSize = size;
    return img.animate([{ maskPosition: from, webkitMaskPosition: from }, { maskPosition: to, webkitMaskPosition: to }],
      { duration: DURATION, easing: EASE, fill: 'forwards' });
  }
  var effects = [
    function circle(img, x, y) { // krąg od miejsca kliknięcia, miękki brzeg
      var r = Math.hypot(Math.max(x, scene.clientWidth - x), Math.max(y, scene.clientHeight - y));
      var d = Math.round(r * 5);
      var g = 'radial-gradient(circle, #000 0, #000 44%, transparent 62%)';
      img.style.maskImage = img.style.webkitMaskImage = g;
      img.style.maskRepeat = img.style.webkitMaskRepeat = 'no-repeat';
      var at = function (size) {
        return { maskSize: size + 'px ' + size + 'px', maskPosition: (x - size / 2) + 'px ' + (y - size / 2) + 'px',
          webkitMaskSize: size + 'px ' + size + 'px', webkitMaskPosition: (x - size / 2) + 'px ' + (y - size / 2) + 'px' };
      };
      return img.animate([at(0), at(d)], { duration: DURATION, easing: EASE, fill: 'forwards' });
    },
    function fromLeft(img) { // zasłona od lewej
      return softMask(img, 'linear-gradient(90deg, #000 0, #000 38%, transparent 62%)', '300% 100%', '100% 0%', '0% 0%');
    },
    function blur(img) { // wyłania się z rozmycia
      return img.animate([{ opacity: 0, filter: 'blur(28px)' }, { opacity: 1, filter: 'blur(0)' }],
        { duration: DURATION + 300, easing: 'ease-in-out', fill: 'forwards' });
    },
    function fromTop(img) { // zasłona z góry
      return softMask(img, 'linear-gradient(180deg, #000 0, #000 38%, transparent 62%)', '100% 300%', '0% 100%', '0% 0%');
    },
    function diagonal(img) { // ukośnie z prawego dolnego rogu
      return softMask(img, 'linear-gradient(315deg, #000 0, #000 38%, transparent 62%)', '300% 300%', '0% 0%', '100% 100%');
    },
    function fromRight(img) { // zasłona od prawej
      return softMask(img, 'linear-gradient(270deg, #000 0, #000 38%, transparent 62%)', '300% 100%', '0% 0%', '100% 0%');
    }
  ];
  function clearMask(img) {
    ['maskImage', 'maskRepeat', 'maskSize', 'maskPosition', 'webkitMaskImage', 'webkitMaskRepeat', 'webkitMaskSize', 'webkitMaskPosition']
      .forEach(function (prop) { img.style[prop] = ''; });
  }

  function show(next, x, y) {
    if (busy || next === current) return;
    busy = true;
    switches += 1;
    closeCard();
    var img = images[next];
    var ready = img.decode ? img.decode().catch(function () {}) : Promise.resolve();
    ready.then(function () {
      var done = function () {
        images[current].classList.remove('scena__obraz--biezacy');
        img.classList.remove('scena__obraz--nowy');
        img.classList.add('scena__obraz--biezacy');
        current = next;
        busy = false;
        update();
      };
      img.classList.add('scena__obraz--nowy');
      if (!canAnimate) { done(); return; }
      var anim = effects[next % effects.length](img, x, y);
      anim.onfinish = function () { done(); anim.cancel(); clearMask(img); };
      setCaption(next);
      sparks(x, y);
      glassFlash();
    });
  }

  function pointOf(event, fallback) {
    if (event.detail === 0 || typeof event.clientX !== 'number') return fallback; // klawiatura
    var rect = scene.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  hotspot.addEventListener('click', function (e) {
    if (isEmpty(current)) { if (fileInput) fileInput.click(); return; }
    var p = pointOf(e, [box.x + box.w / 2, box.y + box.h / 2]);
    show((current + 1) % images.length, p[0], p[1]);
  });
  star.addEventListener('click', function (e) {
    var rect = star.getBoundingClientRect(), base = scene.getBoundingClientRect();
    var p = pointOf(e, [rect.left - base.left + rect.width / 2, rect.top - base.top + rect.height / 2]);
    show((current + 1) % images.length, p[0], p[1]);
  });
  // Rama pokazuje pustą ramę przed wgraniem: domyślnie wybrane zdjęcie zostaje bez zmian
  if (fileInput) {
    emptyLabel.addEventListener('click', function (e) { e.preventDefault(); fileInput.click(); });
  }

  if (window.ResizeObserver) new ResizeObserver(layout).observe(scene);
  else window.addEventListener('resize', layout);
  layout();
  counter.textContent = '1 / ' + images.length;
  caption.textContent = images[0].getAttribute('data-caption') || '';
  var firstFont = images[0].getAttribute('data-font');
  caption.style.fontFamily = firstFont ? '"' + firstFont + '", var(--font-serif)' : '';
  caption.hidden = !caption.textContent;
  update();
})();
