/* S2: kadrowanie (przesuń + przybliż), cena i ocena jakości na żywo. Vanilla JS, bez bibliotek. */
(function () {
  var root = document.getElementById('configure');
  if (!root) return;
  var cards = JSON.parse(document.getElementById('cards-data').textContent);
  var imgW = parseInt(root.dataset.imageWidth, 10);
  var imgH = parseInt(root.dataset.imageHeight, 10);
  var form = document.getElementById('configure-form');
  var stage = document.getElementById('crop-stage');
  var frame = document.getElementById('crop-frame');
  var img = document.getElementById('crop-image');
  var zoom = document.getElementById('crop-zoom');
  var cropInput = form.querySelector('input[name=crop]');
  var quantity = form.querySelector('input[name=quantity]');
  var enhancer = form.querySelector('input[name=enhancer_enabled]');
  var enhancerAuto = document.getElementById('enhancer-auto');
  var submitButton = document.getElementById('submit-button');
  var enhancerTouched = false;

  var state = { z: 1, cx: 0.5, cy: 0.5 };

  function checked(name) { return form.querySelector('input[name=' + name + ']:checked'); }

  function currentCard() {
    var r = checked('format');
    return r ? cards[r.dataset.code] : null;
  }

  function baseCrop() {
    var c = currentCard();
    return c ? c.default_crop : { x: 0, y: 0, w: 1, h: 1, orientation: 'portrait' };
  }

  function currentCrop() {
    var b = baseCrop();
    var w = b.w / state.z, h = b.h / state.z;
    var x = Math.min(Math.max(state.cx - w / 2, 0), 1 - w);
    var y = Math.min(Math.max(state.cy - h / 2, 0), 1 - h);
    return { x: x, y: y, w: w, h: h, orientation: b.orientation };
  }

  // Kadr mieści się w scenie (bez jej paddingu) w proporcji wybranego formatu.
  function render() {
    var crop = currentCrop();
    var cs = getComputedStyle(stage);
    var maxW = stage.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    var maxH = stage.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    var ratio = (crop.w * imgW) / (crop.h * imgH);
    var W = Math.min(maxW, maxH * ratio), H = W / ratio;
    frame.style.width = W + 'px';
    frame.style.height = H + 'px';
    var dispW = W / crop.w, dispH = H / crop.h;
    img.style.width = dispW + 'px';
    img.style.height = dispH + 'px';
    img.style.left = (-crop.x * dispW) + 'px';
    img.style.top = (-crop.y * dispH) + 'px';
    cropInput.value = JSON.stringify(crop);
  }

  function setZoom(z) {
    state.z = Math.min(Math.max(z, 1), 3);
    zoom.value = state.z;
    render();
    scheduleUpdate();
  }

  // Przywrócenie kadru ze szkicu.
  (function restore() {
    if (!cropInput.value) return;
    try {
      var c = JSON.parse(cropInput.value);
      var b = baseCrop();
      if (c && c.w) {
        state.z = Math.min(Math.max(b.w / c.w, 1), 3);
        state.cx = c.x + c.w / 2;
        state.cy = c.y + c.h / 2;
        zoom.value = state.z;
      }
    } catch (e) { /* ignoruj */ }
  })();

  // Przeciąganie; po pierwszym ruchu znika podpowiedź na kadrze.
  var drag = null;
  frame.addEventListener('pointerdown', function (e) {
    drag = { x: e.clientX, y: e.clientY, cx: state.cx, cy: state.cy };
    frame.setPointerCapture(e.pointerId);
    frame.classList.add('crop-frame--moved');
  });
  frame.addEventListener('pointermove', function (e) {
    if (!drag) return;
    var crop = currentCrop();
    var dispW = frame.clientWidth / crop.w, dispH = frame.clientHeight / crop.h;
    state.cx = drag.cx - (e.clientX - drag.x) / dispW;
    state.cy = drag.cy - (e.clientY - drag.y) / dispH;
    render();
  });
  ['pointerup', 'pointercancel'].forEach(function (ev) {
    frame.addEventListener(ev, function () { if (drag) { drag = null; scheduleUpdate(); } });
  });
  zoom.addEventListener('input', function () { setZoom(parseFloat(zoom.value)); });
  root.querySelectorAll('[data-zoom-step]').forEach(function (b) {
    b.addEventListener('click', function () { setZoom(state.z + parseFloat(b.dataset.zoomStep)); });
  });
  document.getElementById('crop-reset').addEventListener('click', function () {
    state.cx = 0.5; state.cy = 0.5; setZoom(1);
  });

  // Ilość: przyciski -/+ w granicach pola.
  root.querySelectorAll('[data-step]').forEach(function (b) {
    b.addEventListener('click', function () {
      var min = parseInt(quantity.min, 10) || 1, max = parseInt(quantity.max, 10) || 99;
      var v = (parseInt(quantity.value, 10) || 1) + parseInt(b.dataset.step, 10);
      quantity.value = Math.min(Math.max(v, min), max);
      scheduleUpdate();
    });
  });

  // Ceny po polsku: 59 albo 59,50.
  function money(value) {
    var n = Number(value);
    if (isNaN(n)) return value;
    return n.toLocaleString('pl-PL', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  }

  function labelText(input, selector) {
    var el = input && input.closest('label').querySelector(selector);
    return el ? el.textContent.trim() : '';
  }

  // Cena i jakość.
  var timer = null;
  function scheduleUpdate() { clearTimeout(timer); timer = setTimeout(update, 200); }
  function update() {
    var card = currentCard();
    if (!card) return;
    var paper = checked('paper');
    var params = new URLSearchParams({
      format: card.id,
      paper: paper ? paper.value : '',
      quantity: quantity.value || '1',
      white_margin: form.querySelector('input[name=white_margin]').checked ? '1' : '0',
      crop: cropInput.value
    });
    fetch(root.dataset.priceUrl + '?' + params.toString(), { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () { /* zostaw poprzednie wartości */ });
  }
  function apply(data) {
    var q = data.quality;
    var label = document.getElementById('quality-label');
    label.textContent = q.verdict_label;
    label.title = 'ok. ' + q.effective_dpi + ' DPI';
    label.className = 'quality quality--' + q.verdict;
    document.getElementById('quality-text').textContent = q.customer_text;
    document.getElementById('price-unit').textContent = money(data.unit_price);
    document.getElementById('price-subtotal').textContent = money(data.subtotal);
    var qty = parseInt(quantity.value, 10) || 1;
    document.getElementById('summary-choice').textContent = [
      labelText(checked('format'), '.format-card__name'),
      'papier ' + labelText(checked('paper'), '.segmented__name').toLowerCase(),
      qty + ' szt.'
    ].join(', ');
    var sug = document.getElementById('quality-suggestion');
    if (q.blocked) {
      submitButton.disabled = true;
      sug.hidden = false;
      sug.textContent = data.suggestion
        ? 'Przy tym przybliżeniu plik jest za słaby. Odsuń kadr albo wybierz ' + data.suggestion.name + '.'
        : 'Przy tym przybliżeniu plik jest za słaby. Odsuń kadr.';
    } else {
      submitButton.disabled = false;
      sug.hidden = true;
    }
    if (q.enhancer_recommended) {
      enhancerAuto.hidden = false;
      if (!enhancerTouched) enhancer.checked = true;
    } else {
      enhancerAuto.hidden = true;
      if (!enhancerTouched) enhancer.checked = false;
    }
  }

  enhancer.addEventListener('change', function () { enhancerTouched = true; });
  form.querySelectorAll('input[name=format]').forEach(function (r) {
    r.addEventListener('change', function () { state.cx = 0.5; state.cy = 0.5; render(); scheduleUpdate(); });
  });
  form.querySelectorAll('input[name=paper], input[name=quantity], input[name=white_margin]').forEach(function (el) {
    el.addEventListener('change', scheduleUpdate);
  });
  form.addEventListener('submit', function () { cropInput.value = JSON.stringify(currentCrop()); });
  window.addEventListener('resize', render);

  if (enhancer.checked) enhancerTouched = true;
  if (img.complete) { render(); update(); } else { img.addEventListener('load', function () { render(); update(); }); }
})();
