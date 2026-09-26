/* ==========================================================================
   PaVoiceAtom — the brand mark as a voice indicator (canvas)
   --------------------------------------------------------------------------
   The helium atom (same geometry as brand-mark.svg) with three states:
     rest     still logo — nucleus, orbit, electrons top + bottom, orbit cut
              around each electron
     voice    both electrons unroll into waves across the canvas, moving with
              a volume envelope: 'audio' (French audio playing, --vk-voice-audio)
              or 'you' (learner speaking, --vk-voice-you)
     loading  the electrons knock each other round the orbit (same motion as
              the CSS .pa-loader)
   Colours come from the page's tokens: --vk-brand-mark, --vk-voice-audio,
   --vk-voice-you. Reduced motion: no travelling — waves grow and shrink in
   place, the loader fades the two electrons in turn.

     const atom = new PaVoiceAtom(canvas);
     await atom.voice('audio', PaVoiceAtom.speechEnvelope('les enfants jouent dehors', 1900));
     atom.startLoading(); … await atom.stopLoading();
     atom.reset();                       // back to rest immediately
   ========================================================================== */
(function () {
  const TAU = Math.PI * 2, TOP = -Math.PI / 2, BOT = Math.PI / 2;
  // brand-mark.svg geometry in a 100-unit box
  const G = { R: 33, sw: 9, nr: 10.5, er: 10.5, cut: 4.5 };
  // Loader timing (matches .pa-loader): fall, pause at the top, settle after a strike
  const FALL = 0.38, HANG = 0.08, SETTLE = 0.22;

  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeIO = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const easeOut = t => 1 - Math.pow(1 - t, 3);
  const easeInQ = t => t * t, easeOutQ = t => 1 - (1 - t) * (1 - t);
  const now = () => performance.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const rmq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const reduced = () => !!(rmq && rmq.matches);

  function rgb(css) {
    const h = css.trim().replace('#', '');
    const f = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
    const n = parseInt(f, 16);
    return [n >> 16 & 255, n >> 8 & 255, n & 255];
  }
  function mix(a, b, t) {
    const A = rgb(a), B = rgb(b);
    return 'rgb(' + A.map((v, i) => Math.round(lerp(v, B[i], t))).join(',') + ')';
  }

  const atoms = [];
  let rafOn = false, last = 0;
  function frame(t) {
    const dt = Math.min(0.05, (t - last) / 1000); last = t;
    atoms.forEach(a => a._step(dt, t));
    requestAnimationFrame(frame);
  }

  class PaVoiceAtom {
    constructor(canvas) {
      this.cv = canvas; this.ctx = canvas.getContext('2d');
      this.ang = [TOP, BOT]; this.alpha = [1, 1];
      this.morph = 0; this.amp = 0; this.kind = null; this.env = null; this.phase = 0;
      this.loading = null; this.tweens = []; this._gen = 0;
      this._readColors();
      if (window.ResizeObserver) new ResizeObserver(() => this._resize()).observe(canvas);
      this._resize();
      atoms.push(this);
      if (!rafOn) { rafOn = true; last = now(); requestAnimationFrame(frame); }
    }

    // A speech-like volume envelope for text lasting durationMs: one bump per
    // vowel group, louder and longer on the last (French phrase-final stress).
    static speechEnvelope(text, durationMs) {
      const groups = (text.toLowerCase().match(/[aeiouyàâäéèêëîïôöûùü]+/g) || ['a']).length;
      const unit = (durationMs / 1000 - 0.25) / (groups + 0.8);
      const bumps = [];
      let t = 0.12;
      for (let i = 0; i < groups; i++) {
        const d = i === groups - 1 ? unit * 1.8 : unit;
        const h = i === groups - 1 ? 1 : 0.5 + 0.4 * Math.abs(Math.sin(i * 2.3 + text.length));
        bumps.push({ c: t + d * 0.45, w: d * 0.6, h });
        t += d;
      }
      return { dur: durationMs, fn: x => {
        let v = 0.07;
        bumps.forEach(b => { const z = (x - b.c) / b.w; v += b.h * Math.exp(-z * z); });
        return Math.min(1, v);
      } };
    }

    // Waves for the length of the envelope, then back to rest. kind: 'audio' | 'you'.
    // opts (ms): inMs electrons → waves (800), fadeMs waves go quiet (320),
    // outMs waves → electrons (750).
    async voice(kind, env, opts) {
      const o = Object.assign({ inMs: 800, fadeMs: 320, outMs: 750 }, opts);
      const gen = this._gen;
      this.kind = kind;
      await this._tween(v => { this.morph = v; }, 0, 1, o.inMs);
      if (gen !== this._gen) return;
      this.env = { fn: env.fn, t0: now() };
      await sleep(env.dur);
      if (gen !== this._gen) return;
      this.env = null;
      await this._tween(v => { this.amp = v; }, this.amp, 0, o.fadeMs, easeOut);
      await this._tween(v => { this.morph = v; }, 1, 0, o.outMs);
      if (gen === this._gen) this.kind = null;
    }

    startLoading() { this.ang = [TOP, BOT]; this.loading = { t0: now(), stop: false, done: null }; }
    // Resolves once the electrons are back at the top and bottom.
    stopLoading() {
      if (!this.loading) return Promise.resolve();
      return new Promise(r => { this.loading.stop = true; this.loading.done = r; });
    }

    reset() {
      this._gen++;
      this.tweens = []; this.env = null; this.loading = null;
      this.morph = 0; this.amp = 0; this.kind = null;
      this.ang = [TOP, BOT]; this.alpha = [1, 1];
      this._draw();
    }

    _readColors() {
      const cs = getComputedStyle(this.cv);
      const g = n => cs.getPropertyValue(n).trim();
      this.c = { mark: g('--vk-brand-mark') || '#2D6CB3', audio: g('--vk-voice-audio') || '#2D6CB3', you: g('--vk-voice-you') || '#B37D12' };
    }

    _resize() {
      // Layout size, not getBoundingClientRect: a CSS scale-in must not shrink the drawing
      const w = this.cv.clientWidth, h = this.cv.clientHeight, d = window.devicePixelRatio || 1;
      if (w === this.w && h === this.h && d === this._dpr) return;
      this.w = w; this.h = h; this._dpr = d;
      this.cv.width = Math.max(1, Math.round(w * d));
      this.cv.height = Math.max(1, Math.round(h * d));
      this.ctx.setTransform(d, 0, 0, d, 0, 0);
      this.S = Math.min(this.h * 0.84, 64);                    // the mark's box
      this.L = Math.max(this.S / 2, Math.min(this.w / 2 - 8, 300)); // wave half-width
      this.Hw = Math.min(this.h * 0.42, 32);                    // wave height
      this.cx = this.w / 2; this.cy = this.h / 2;
      this._draw();
    }

    _tween(set, from, to, dur, ease) {
      if (reduced()) dur *= 0.5;
      return new Promise(res => {
        set(from);
        this.tweens.push({ set, from, to, dur, ease: ease || easeIO, t0: now(), res });
      });
    }

    _step(dt, t) {
      // Draw while anything moves, plus the frame where it comes to rest
      const wasActive = this.morph > 0 || this.loading || this.tweens.length;
      for (let i = this.tweens.length - 1; i >= 0; i--) {
        const tw = this.tweens[i], p = tw.dur <= 0 ? 1 : clamp((t - tw.t0) / tw.dur, 0, 1);
        tw.set(lerp(tw.from, tw.to, tw.ease(p)));
        if (p >= 1) { this.tweens.splice(i, 1); tw.res(); }
      }
      if (!reduced()) this.phase += dt;
      if (this.env) {
        const target = this.env.fn((t - this.env.t0) / 1000);
        this.amp += (target - this.amp) * Math.min(1, dt * (reduced() ? 4 : 9));
      }
      if (this.loading) this._stepLoading(t);
      if (wasActive || this.morph > 0 || this.loading || this.tweens.length) this._draw();
    }

    _stepLoading(t) {
      const L = this.loading, e = (t - L.t0) / 1000;
      const gap = (2 * G.er) / G.R;
      const finish = () => { this.ang = [TOP, BOT]; this.alpha = [1, 1]; this.loading = null; if (L.done) L.done(); };
      if (reduced()) {
        const s = Math.sin(e * TAU / 1.3);
        this.ang = [TOP, BOT];
        this.alpha = [0.3 + 0.7 * (0.5 + 0.5 * s), 0.3 + 0.7 * (0.5 - 0.5 * s)];
        if (L.stop) finish();
        return;
      }
      this.alpha = [1, 1];
      const P = 2 * FALL + HANG;
      if (e < FALL) { this.ang = [TOP + (Math.PI - gap) * easeInQ(e / FALL), BOT]; return; }
      const k = Math.floor((e - FALL) / P), r = (e - FALL) - k * P;
      const striker = k % 2 === 0 ? 0 : 1, struck = 1 - striker, a = [0, 0];
      a[striker] = BOT - gap * (1 - easeOutQ(clamp(r / SETTLE, 0, 1)));
      if (r < FALL) a[struck] = BOT + Math.PI * easeOutQ(r / FALL);
      else if (r < FALL + HANG) { a[struck] = BOT + Math.PI; if (L.stop) { finish(); return; } }
      else a[struck] = BOT + Math.PI + (Math.PI - gap) * easeInQ((r - FALL - HANG) / FALL);
      this.ang = a;
    }

    _ripple(u, i) {
      const t = this.phase;
      return i === 0
        ? 0.65 * Math.sin(TAU * 2.4 * u - 6.2 * t) + 0.35 * Math.sin(TAU * 5.1 * u + 3.9 * t)
        : 0.8 * (0.6 * Math.sin(TAU * 3.1 * u - 7.4 * t + 2) + 0.4 * Math.sin(TAU * 6.3 * u + 4.8 * t));
    }

    _draw() {
      const c = this.ctx;
      c.clearRect(0, 0, this.w, this.h);
      if (!this.S) return;
      const s = this.S / 100, cx = this.cx, cy = this.cy;
      const R = G.R * s, sw = Math.max(1.5, G.sw * s), er = Math.max(2, G.er * s);
      const nr = Math.max(2, G.nr * s), cut = Math.max(0.8, G.cut * s);
      const m = this.morph, ink = this.c.mark;
      const col = m > 0 && this.kind ? mix(ink, this.c[this.kind], clamp(m * 1.8, 0, 1)) : ink;

      // Orbit — fades back while a voice is showing
      c.globalAlpha = 1 - 0.75 * m;
      c.strokeStyle = ink; c.lineWidth = sw;
      c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
      c.globalAlpha = 1;
      // Cut the orbit round each electron; the cut closes as they leave to become waves
      const cutR = (er + cut) * (1 - m);
      if (cutR > 0.3) {
        c.globalCompositeOperation = 'destination-out';
        this.ang.forEach(a => { c.beginPath(); c.arc(cx + R * Math.cos(a), cy + R * Math.sin(a), cutR, 0, TAU); c.fill(); });
        c.globalCompositeOperation = 'source-over';
      }
      // Nucleus — pulses gently with the voice
      c.fillStyle = col;
      c.beginPath(); c.arc(cx, cy, nr * (1 + 0.3 * this.amp * m), 0, TAU); c.fill();

      if (m <= 0.001) {
        for (let i = 0; i < 2; i++) {
          c.globalAlpha = this.alpha[i]; c.fillStyle = ink;
          c.beginPath(); c.arc(cx + R * Math.cos(this.ang[i]), cy + R * Math.sin(this.ang[i]), er, 0, TAU); c.fill();
        }
        c.globalAlpha = 1;
        return;
      }
      // Electrons stretch along the orbit, straighten, then become the waves
      c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = col;
      const e1 = easeOut(clamp(m / 0.35, 0, 1)), e2 = easeIO(clamp((m - 0.3) / 0.7, 0, 1)), N = 120;
      for (let i = 0; i < 2; i++) {
        const a0 = this.ang[i], span = 0.02 + 1.2 * e1, dir = Math.sin(a0) < 0 ? 1 : -1;
        c.beginPath();
        for (let j = 0; j < N; j++) {
          const u = j / (N - 1), a = a0 + dir * (u - 0.5) * span;
          const ax = cx + R * Math.cos(a), ay = cy + R * Math.sin(a);
          const wx = cx - this.L + u * 2 * this.L;
          const wy = cy + this._ripple(u, i) * Math.pow(Math.sin(Math.PI * u), 1.4) * this.Hw * this.amp;
          const x = lerp(ax, wx, e2), y = lerp(ay, wy, e2);
          j ? c.lineTo(x, y) : c.moveTo(x, y);
        }
        c.lineWidth = lerp(er * 2, sw * 0.8, clamp(m / 0.55, 0, 1));
        c.stroke();
      }
    }
  }

  window.PaVoiceAtom = PaVoiceAtom;
})();
