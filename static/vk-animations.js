/*!
 * vraiKronos — Dashboard Animations
 * v1.0.0 | Vraifactors
 *
 * Entrance animations, count-up counters, SVG line draw-in, ring draw-in.
 * Gated on .vk-js-animations added to <html> so elements stay visible
 * without JS. Respects prefers-reduced-motion throughout.
 *
 * Usage
 * ─────────────────────────────────────────────────────────────────────────
 * Include after components-core.css. Works automatically — no init needed.
 *
 * Count-up:
 *   <span class="vk-count-up"
 *         data-target="24817"
 *         data-decimals="0"
 *         data-prefix=""
 *         data-suffix=""
 *         data-duration="1400">0</span>
 *
 * All .vk-chart, .vk-kpi-card, .vk-data-table-wrap, and .vk-ring-chart
 * elements are observed automatically.
 */
(function () {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ── Gate class — CSS animations only apply when this is present ──────────
  document.documentElement.classList.add('vk-js-animations');


  // ════════════════════════════════════════════════════════════════════════
  // Easing
  // ════════════════════════════════════════════════════════════════════════

  function easeOutExpo(t) {
    return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
  }


  // ════════════════════════════════════════════════════════════════════════
  // Count-up
  // Reads data-target, data-decimals, data-prefix, data-suffix,
  // data-separator (default true), data-duration (ms, default 1400).
  // ════════════════════════════════════════════════════════════════════════

  function runCountUp(el) {
    if (el.dataset.animated) return;
    el.dataset.animated = '1';

    const target    = parseFloat(el.dataset.target   ?? 0);
    const decimals  = parseInt(el.dataset.decimals   ?? 0);
    const prefix    = el.dataset.prefix              ?? '';
    const suffix    = el.dataset.suffix              ?? '';
    const separator = el.dataset.separator           !== 'false';
    const duration  = parseInt(el.dataset.duration   ?? 1400);

    function fmt(n) {
      const fixed = n.toFixed(decimals);
      if (!separator) return prefix + fixed + suffix;
      const parts = fixed.split('.');
      const int   = parseInt(parts[0]).toLocaleString();
      return prefix + (parts[1] !== undefined ? int + '.' + parts[1] : int) + suffix;
    }

    if (reducedMotion) {
      el.textContent = fmt(target);
      return;
    }

    const start = performance.now();

    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
      el.textContent = fmt(target * easeOutExpo(progress));
      if (progress < 1) requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
  }


  // ════════════════════════════════════════════════════════════════════════
  // SVG line draw-in
  // Uses getTotalLength() to set stroke-dasharray/offset then transitions
  // dashoffset to 0 for a path-trace effect.
  // ════════════════════════════════════════════════════════════════════════

  function animateLine(path) {
    if (reducedMotion || path.dataset.animated) return;
    path.dataset.animated = '1';

    const len = path.getTotalLength();
    path.style.transition       = 'none';
    path.style.strokeDasharray  = len;
    path.style.strokeDashoffset = len;

    // Double rAF to force paint before starting transition
    requestAnimationFrame(() => requestAnimationFrame(() => {
      path.style.transition      = 'stroke-dashoffset 900ms cubic-bezier(0.16, 1, 0.3, 1)';
      path.style.strokeDashoffset = 0;
    }));
  }


  // ════════════════════════════════════════════════════════════════════════
  // Ring / donut draw-in
  // Reads the resolved computed stroke-dasharray, resets to 0, then
  // transitions each segment in with a stagger.
  // ════════════════════════════════════════════════════════════════════════

  function animateRingSvg(svg) {
    if (reducedMotion) return;
    const segs = svg.querySelectorAll('.vk-ring-seg');
    if (!segs.length) return;

    // Capture computed target values before resetting
    const targets = Array.from(segs).map(seg =>
      window.getComputedStyle(seg).strokeDasharray
    );

    // Reset all to invisible (no transition)
    segs.forEach(seg => {
      seg.style.transition      = 'none';
      seg.style.strokeDasharray = '0 314.159px';
    });

    // Reflow
    svg.getBoundingClientRect();

    // Animate each segment with stagger
    segs.forEach((seg, i) => {
      setTimeout(() => {
        seg.style.transition      = 'stroke-dasharray 520ms cubic-bezier(0.16, 1, 0.3, 1)';
        seg.style.strokeDasharray = targets[i];
      }, i * 90);
    });
  }


  // ════════════════════════════════════════════════════════════════════════
  // Bar stagger — sets --vk-anim-delay per bar so CSS can use it
  // ════════════════════════════════════════════════════════════════════════

  function staggerBars(chart) {
    if (reducedMotion) return;
    chart.querySelectorAll('.vk-bar').forEach((bar, i) => {
      bar.style.setProperty('--vk-anim-delay', (i * 40) + 'ms');
    });
  }


  // ════════════════════════════════════════════════════════════════════════
  // Metric row stagger
  // ════════════════════════════════════════════════════════════════════════

  function staggerMetricRows(chart) {
    if (reducedMotion) return;
    chart.querySelectorAll('.vk-metric-row').forEach((row, i) => {
      row.style.setProperty('--vk-anim-delay', (i * 75) + 'ms');
    });
  }


  // ════════════════════════════════════════════════════════════════════════
  // KPI card stagger (when multiple are siblings in a grid)
  // ════════════════════════════════════════════════════════════════════════

  function staggerKpiCards(container) {
    if (reducedMotion) return;
    container.querySelectorAll('.vk-kpi-card').forEach((card, i) => {
      card.style.setProperty('--vk-anim-delay', (i * 60) + 'ms');
    });
  }


  // ════════════════════════════════════════════════════════════════════════
  // On visible — runs all animations for a given element
  // ════════════════════════════════════════════════════════════════════════

  function onVisible(el) {
    el.classList.add('is-visible');

    // Count-up numbers inside this element
    el.querySelectorAll('.vk-count-up').forEach(runCountUp);
    // Or if the element itself is a count-up
    if (el.classList.contains('vk-count-up')) runCountUp(el);

    // SVG line paths (series lines in line charts)
    el.querySelectorAll('.series-1-line, .series-2-line').forEach(animateLine);

    // Ring segments
    el.querySelectorAll('.vk-ring-svg').forEach(animateRingSvg);

    // Bars and rows
    staggerBars(el);
    staggerMetricRows(el);
  }


  // ════════════════════════════════════════════════════════════════════════
  // Intersection Observer
  // ════════════════════════════════════════════════════════════════════════

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      onVisible(entry.target);
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12 });

  function observeAll() {
    // Stagger KPI cards that share a parent grid
    document.querySelectorAll('.vk-dashboard-row').forEach(staggerKpiCards);

    document.querySelectorAll(
      '.vk-chart, .vk-kpi-card, .vk-data-table-wrap, .vk-ring-chart'
    ).forEach(el => observer.observe(el));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', observeAll);
  } else {
    observeAll();
  }

})();
