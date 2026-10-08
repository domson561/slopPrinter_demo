/* Atrapa GH: auto-symulacja - gdy włączona, strona co N sekund woła /gh-mock/tick/ i odświeża listę. */
(function () {
  var form = document.getElementById('auto-form');
  if (!form || form.dataset.auto !== '1') return;
  var interval = Math.max(2, parseInt(form.dataset.interval, 10) || 10) * 1000;
  var csrf = form.querySelector('input[name=csrfmiddlewaretoken]').value;
  var status = document.getElementById('auto-status');
  var remaining = interval / 1000;

  function tick() {
    fetch(form.dataset.tickUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'fetch' }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.auto) { window.location.reload(); return; }
        if (data.advanced > 0) { window.location.reload(); return; }
        remaining = interval / 1000;
      })
      .catch(function () { /* spróbuj przy następnym ticku */ });
  }
  setInterval(tick, interval);
  setInterval(function () {
    remaining = Math.max(0, remaining - 1);
    status.textContent = 'auto: włączona, następny krok za ' + remaining + ' s';
  }, 1000);
})();
