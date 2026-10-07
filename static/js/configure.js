/* S2: kadrowanie (przesuń + przybliż), cena i ocena jakości na żywo. Vanilla JS, bez bibliotek. */
(function () {
  var root = document.getElementById('configure');
  if (!root) return;
  var cards = JSON.parse(document.getElementById('cards-data').textContent);
  var imgW = parseInt(root.dataset.imageWidth, 10);
  var imgH = parseInt(root.dataset.imageHeight, 10);
  var form = document.getElementById('configure-form');
  var frame = document.getElementById('crop-frame');
  var img = document.getElementById('crop-image');
  var zoom = document.getElementById('crop-zoom');
  var cropInput = form.querySelector('input[name=crop]');
  var enhancer = form.querySelector('input[name=enhancer_enabled]');
  var enhancerAuto = document.getElementById('enhancer-auto');
  var submitButton = document.getElementById('submit-button');
  var enhancerTouched = false;

  var state = { z: 1, cx: 0.5, cy: 0.5 };

  function currentCard() {
    var r = form.querySelector('input[name=format]:checked');
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

  function render() {
    var crop = currentCrop();
    var W = frame.clientWidth;
    var ratio = (crop.w * imgW) / (crop.h * imgH);
    var H = W / ratio;
    frame.style.height = H + 'px';
    var dispW = W / crop.w, dispH = H / crop.h;
    img.style.width = dispW + 'px';
    img.style.height = dispH + 'px';
    img.style.left = (-crop.x * dispW) + 'px';
    img.style.top = (-crop.y * dispH) + 'px';
    cropInput.value = JSON.stringify(crop);
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

  // Przeciąganie.
  var drag = null;
  frame.addEventListener('pointerdown', function (e) {
    drag = { x: e.clientX, y: e.clientY, cx: state.cx, cy: state.cy };
    frame.setPointerCapture(e.pointerId);
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
  zoom.addEventListener('input', function () { state.z = parseFloat(zoom.value); render(); scheduleUpdate(); });
  document.getElementById('crop-reset').addEventListener('click', function () {
    state.cx = 0.5; state.cy = 0.5; state.z = 1; zoom.value = 1; render(); scheduleUpdate();
  });

  // Cena i jakość.
  var timer = null;
  function scheduleUpdate() { clearTimeout(timer); timer = setTimeout(update, 200); }
  function update() {
    var card = currentCard();
    if (!card) return;
    var params = new URLSearchParams({
      format: card.id,
      paper: form.querySelector('select[name=paper]').value,
      quantity: form.querySelector('input[name=quantity]').value || '1',
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
    label.textContent = q.verdict_label + ' (ok. ' + q.effective_dpi + ' DPI)';
    label.className = 'quality-badge quality-badge--' + q.verdict;
    document.getElementById('quality-text').textContent = q.customer_text;
    document.getElementById('price-unit').textContent = data.unit_price;
    document.getElementById('price-subtotal').textContent = data.subtotal;
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
  ['select[name=paper]', 'input[name=quantity]', 'input[name=white_margin]'].forEach(function (sel) {
    form.querySelector(sel).addEventListener('change', scheduleUpdate);
  });
  form.addEventListener('submit', function () { cropInput.value = JSON.stringify(currentCrop()); });
  window.addEventListener('resize', render);

  if (enhancer.checked) enhancerTouched = true;
  if (img.complete) { render(); update(); } else { img.addEventListener('load', function () { render(); update(); }); }
})();
