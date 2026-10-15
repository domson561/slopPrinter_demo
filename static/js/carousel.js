/*
  Karuzela na stronie głównej: przewijany pasek ze scroll-snap, strzałki, kropki i automatyczne
  przewijanie. Przy jednym slajdzie sterowanie zostaje ukryte.
*/
(function () {
  var INTERVAL_MS = 6000;
  var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  document.querySelectorAll('[data-carousel]').forEach(function (root) {
    var track = root.querySelector('[data-carousel-track]');
    var slides = track.children;
    if (slides.length < 2) return;

    var prev = root.querySelector('[data-carousel-prev]');
    var next = root.querySelector('[data-carousel-next]');
    var dotsBox = root.querySelector('[data-carousel-dots]');
    var dots = [];
    var current = 0;
    var timer = null;

    Array.prototype.forEach.call(slides, function (slide, i) {
      slide.setAttribute('aria-roledescription', 'slajd');
      slide.setAttribute('aria-label', (i + 1) + ' z ' + slides.length);
      var dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'carousel__dot';
      dot.setAttribute('aria-label', 'Slajd ' + (i + 1));
      dot.addEventListener('click', function () { go(i); restart(); });
      dotsBox.appendChild(dot);
      dots.push(dot);
    });

    function go(i) {
      current = (i + slides.length) % slides.length;
      track.scrollTo({ left: current * track.clientWidth, behavior: reducedMotion ? 'auto' : 'smooth' });
      mark();
    }

    function mark() {
      dots.forEach(function (d, i) { d.setAttribute('aria-current', i === current ? 'true' : 'false'); });
    }

    function restart() {
      clearInterval(timer);
      if (!reducedMotion) timer = setInterval(function () { go(current + 1); }, INTERVAL_MS);
    }

    prev.addEventListener('click', function () { go(current - 1); restart(); });
    next.addEventListener('click', function () { go(current + 1); restart(); });

    // Przewinięcie palcem lub gładzikiem też zmienia bieżący slajd
    var scrollEnd = null;
    track.addEventListener('scroll', function () {
      clearTimeout(scrollEnd);
      scrollEnd = setTimeout(function () {
        current = Math.round(track.scrollLeft / track.clientWidth);
        mark();
      }, 80);
    });

    root.addEventListener('mouseenter', function () { clearInterval(timer); });
    root.addEventListener('mouseleave', restart);
    root.addEventListener('focusin', function () { clearInterval(timer); });
    root.addEventListener('focusout', restart);

    prev.hidden = next.hidden = dotsBox.hidden = false;
    mark();
    restart();
  });
})();
