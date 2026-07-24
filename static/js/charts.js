/* DomoPi charts — rendu SVG sans dépendance.
   Mode "raw"   : une courbe (mesures brutes, pas = intervalle de collecte).
   Mode "hourly"/"daily" : courbe moyenne + bande min-max (3 courbes).

   Zoom (voir plus bas) : rectangle à la souris, pincement à deux doigts.
   Il ne s'agit que d'un changement d'échelle sur les points déjà chargés —
   aucune requête n'est refaite au serveur. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  let seq = 0;                       // identifiants uniques des clipPath

  function el(tag, attrs) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function niceTicks(min, max, n) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min, step0 = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step)
      ticks.push(+v.toFixed(6));
    return ticks;
  }

  /* Valeurs numériques : réglages d'affichage du visualiseur (app.js expose
     window.fmtNum) ; repli sur l'ancien format si chargé sans app.js. */
  const fmtNum = v => window.fmtNum ? window.fmtNum(v)
    : (Math.abs(v) >= 1000 ? v.toFixed(0) : +v.toFixed(2));

  function fmtTime(ts, spanS) {
    const d = new Date(ts * 1000);
    if (spanS <= 3600) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    if (spanS <= 2 * 86400) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    if (spanS <= 40 * 86400) return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
    return d.toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
  }

  /* Ramène [a,b] à l'intérieur de [fa,fb] sans changer son amplitude
     (l'amplitude est d'abord bornée à celle de la plage complète) : sert de
     garde-fou au pincement, qui ne peut donc pas dézoomer au-delà de la vue
     d'origine ni sortir des données. */
  function clampAxis(a, b, fa, fb) {
    const span = Math.min(b - a, fb - fa);
    if (a < fa) a = fa;
    if (a + span > fb) a = fb - span;
    return [a, a + span];
  }

  /* container: élément DOM ; data: {mode, points} ;
     opts: {unit, height, view, onZoom}
       view   — vue initiale {t0,t1,vmin,vmax} (restauration d'un zoom après
                un rafraîchissement de page), null = vue complète
       onZoom — rappelé à chaque changement de zoom avec la vue courante
                (null quand on revient à la vue d'origine) */
  window.renderChart = function (container, data, opts) {
    opts = opts || {};
    container.innerHTML = "";
    const W = 640, H = opts.height || 260;
    const M = { l: 46, r: 10, t: 10, b: 24 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img" });
    const pts = data.points || [];
    if (!pts.length) {
      const t = el("text", { x: W / 2, y: H / 2, fill: "#8b97a5", "text-anchor": "middle", "font-size": 13 });
      t.textContent = "Aucune donnée sur cette période";
      svg.appendChild(t); container.appendChild(svg); return;
    }
    const band = data.mode !== "raw";
    const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
    let vmin = Infinity, vmax = -Infinity;
    for (const p of pts) {
      const lo = band ? p.min : p.v, hi = band ? p.max : p.v;
      if (lo < vmin) vmin = lo;
      if (hi > vmax) vmax = hi;
    }
    const pad = (vmax - vmin) * 0.08 || 1;
    const full = { t0, t1: Math.max(t1, t0 + 1), vmin: vmin - pad, vmax: vmax + pad };

    /* Pile de zooms : le sommet est la vue affichée, "Zoom précédent" dépile,
       "Zoom initial" vide la pile. `live` est la vue en cours de manipulation
       (pincement), empilée seulement quand le geste se termine. */
    const stack = [];
    let live = null;
    if (opts.view && opts.view.t1 > opts.view.t0 && opts.view.vmax > opts.view.vmin)
      stack.push({ t0: opts.view.t0, t1: opts.view.t1, vmin: opts.view.vmin, vmax: opts.view.vmax });

    let vw = full;                       // vue courante (mise à jour par render)
    let visLo = 0, visHi = pts.length - 1; // indices des points visibles
    const plotW = W - M.l - M.r, plotH = H - M.t - M.b;
    const X = t => M.l + (t - vw.t0) / Math.max(1e-9, vw.t1 - vw.t0) * plotW;
    const Y = v => H - M.b - (v - vw.vmin) / Math.max(1e-9, vw.vmax - vw.vmin) * plotH;
    const invX = px => vw.t0 + (px - M.l) / plotW * (vw.t1 - vw.t0);
    const invY = py => vw.vmin + (H - M.b - py) / plotH * (vw.vmax - vw.vmin);

    /* Lever de crayon : une interruption de mesures (valeur invalide d'un
       capteur calculé, capteur muet...) casse le tracé au lieu de tirer un
       trait sur le trou. Seuil : écart > 1.5 x l'écart médian entre points. */
    const dts = pts.slice(1).map((p, i) => p.t - pts[i].t).sort((a, b) => a - b);
    const maxDt = Math.max(90, (dts[Math.floor(dts.length / 2)] || 0) * 1.5);

    const clipId = "chart-clip-" + (++seq);

    /* ------------------------------------------------------------ rendu */
    function render() {
      vw = live || stack[stack.length - 1] || full;
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      const defs = el("defs", {});
      const cp = el("clipPath", { id: clipId });
      cp.appendChild(el("rect", { x: M.l, y: M.t, width: plotW, height: plotH }));
      defs.appendChild(cp); svg.appendChild(defs);

      for (const v of niceTicks(vw.vmin, vw.vmax, 5)) {
        svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v),
          stroke: "#2b3442", "stroke-width": .6 }));
        const t = el("text", { x: M.l - 6, y: Y(v) + 3.5, fill: "#8b97a5",
          "font-size": 10, "text-anchor": "end", "font-family": "monospace" });
        t.textContent = fmtNum(v);
        svg.appendChild(t);
      }
      const nx = 5, spanS = vw.t1 - vw.t0;
      for (let i = 0; i <= nx; i++) {
        const ts = vw.t0 + spanS * i / nx;
        const t = el("text", { x: X(ts), y: H - 7, fill: "#8b97a5",
          "font-size": 10, "text-anchor": i === 0 ? "start" : i === nx ? "end" : "middle" });
        t.textContent = fmtTime(ts, spanS);
        svg.appendChild(t);
      }

      /* Points visibles, débordant d'un cran de chaque côté pour que les
         courbes atteignent les bords du cadre au lieu de s'arrêter au dernier
         point strictement dans la fenêtre. */
      let i0 = 0, i1 = pts.length - 1;
      while (i0 < i1 && pts[i0 + 1].t < vw.t0) i0++;
      while (i1 > i0 && pts[i1 - 1].t > vw.t1) i1--;
      visLo = i0; visHi = i1;
      const vis = pts.slice(i0, i1 + 1);

      const segs = [];
      let seg = [vis[0]];
      for (let i = 1; i < vis.length; i++) {
        if (vis[i].t - vis[i - 1].t > maxDt) { segs.push(seg); seg = []; }
        seg.push(vis[i]);
      }
      segs.push(seg);

      const g = el("g", { "clip-path": `url(#${clipId})` });
      const path = (s, key) => s.map((p, i) =>
        (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(band ? p[key] : p.v).toFixed(1)).join("");
      const dot = (p, key, color, r) =>
        g.appendChild(el("circle", { cx: X(p.t).toFixed(1),
          cy: Y(band ? p[key] : p.v).toFixed(1), r, fill: color }));

      for (const s of segs) {
        if (s.length === 1) {           // point isolé : un disque, sinon rien ne se verrait
          if (band) { dot(s[0], "max", "#e05b4f", 1.4); dot(s[0], "min", "#4f9de0", 1.4); }
          dot(s[0], "avg", "#e8a13c", 1.8);
          continue;
        }
        if (band) {
          const up = path(s, "max");
          const down = s.slice().reverse().map(p =>
            "L" + X(p.t).toFixed(1) + " " + Y(p.min).toFixed(1)).join("");
          g.appendChild(el("path", { d: up + down + "Z", fill: "#e8a13c", "fill-opacity": .14, stroke: "none" }));
          g.appendChild(el("path", { d: path(s, "max"), fill: "none", stroke: "#e05b4f", "stroke-width": 1 }));
          g.appendChild(el("path", { d: path(s, "min"), fill: "none", stroke: "#4f9de0", "stroke-width": 1 }));
          g.appendChild(el("path", { d: path(s, "avg"), fill: "none", stroke: "#e8a13c", "stroke-width": 1.8 }));
        } else {
          g.appendChild(el("path", { d: path(s, "v"), fill: "none", stroke: "#e8a13c",
            "stroke-width": 1.6, "stroke-linejoin": "round" }));
        }
      }
      svg.appendChild(g);
      sel = null;                       // le rectangle de sélection a été effacé
    }

    let raf = 0;                        // rendu au rythme de l'écran pendant un geste
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; render(); });
    };

    /* ---------------------------------------------------------- curseur */
    /* Curseur au survol : ligne verticale accrochée au point réel le plus
       proche, avec étiquette date (+ heure en mode "raw", <= 4 j) et
       valeur(s) courante(s). Position verticale de l'étiquette fixe (haut du
       graphe) — seule l'abscisse suit la souris. */
    const fmtVal = v => fmtNum(v) +
      (opts.unit ? " " + opts.unit : "");
    const fmtCursorDate = (ts, withTime) => {
      const d = new Date(ts * 1000);
      const day = d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" });
      return withTime ? day + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : day;
    };
    const nearestIdx = t => {
      let lo = 0, hi = pts.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid].t < t) lo = mid + 1; else hi = mid; }
      if (lo > 0 && Math.abs(pts[lo - 1].t - t) <= Math.abs(pts[lo].t - t)) lo--;
      return Math.max(visLo, Math.min(visHi, lo));   // jamais un point hors cadre
    };
    const removeCursor = () => svg.querySelectorAll(".cursor-g").forEach(n => n.remove());
    const showCursor = px => {
      removeCursor();
      const p = pts[nearestIdx(invX(px))];
      const cx = X(p.t);
      const g = el("g", { class: "cursor-g" });
      g.appendChild(el("line", { x1: cx, x2: cx, y1: M.t, y2: H - M.b,
        stroke: "#8b97a5", "stroke-width": .8, "stroke-dasharray": "3,2" }));
      // Les pastilles sont découpées au cadre (zoomé en Y, le point visé peut
      // tomber hors fenêtre) ; l'étiquette, elle, donne toujours les valeurs.
      const gp = el("g", { "clip-path": `url(#${clipId})` });
      g.appendChild(gp);
      const lines = [{ text: fmtCursorDate(p.t, !band), color: "#c8d0d8" }];
      if (band) {
        gp.appendChild(el("circle", { cx, cy: Y(p.max), r: 2.6, fill: "#e05b4f" }));
        gp.appendChild(el("circle", { cx, cy: Y(p.avg), r: 2.6, fill: "#e8a13c" }));
        gp.appendChild(el("circle", { cx, cy: Y(p.min), r: 2.6, fill: "#4f9de0" }));
        lines.push({ text: "max " + fmtVal(p.max), color: "#e05b4f" });
        lines.push({ text: "moy " + fmtVal(p.avg), color: "#e8a13c" });
        lines.push({ text: "min " + fmtVal(p.min), color: "#4f9de0" });
      } else {
        gp.appendChild(el("circle", { cx, cy: Y(p.v), r: 2.8, fill: "#e8a13c" }));
        lines.push({ text: fmtVal(p.v), color: "#e8a13c" });
      }
      // Groupe séparé pour l'étiquette : sa bbox propre (hors ligne/points,
      // qui s'étendent sur toute la hauteur du graphe) donne la taille du fond.
      const lh = 12.5, padX = 6, padY = 5;
      const tip = el("g", {});
      const texts = lines.map((ln, i) => {
        const txt = el("text", { x: 0, y: padY + (i + 1) * lh - 3, "font-size": 10.5, fill: ln.color });
        txt.textContent = ln.text;
        tip.appendChild(txt);
        return txt;
      });
      g.appendChild(tip);
      svg.appendChild(g);
      const bbox = tip.getBBox();
      const boxW = bbox.width + padX * 2, boxH = lines.length * lh + padY * 2;
      let boxX = cx + 8;
      if (boxX + boxW > W - M.r) boxX = cx - 8 - boxW;
      const boxY = M.t + 4;
      const rect = el("rect", { x: boxX, y: boxY, width: boxW, height: boxH, rx: 4,
        fill: "#161c24", stroke: "#2b3442", "stroke-width": 1 });
      tip.insertBefore(rect, texts[0]);
      texts.forEach((txt, i) => {
        txt.setAttribute("x", boxX + padX);
        txt.setAttribute("y", boxY + padY + (i + 1) * lh - 3);
      });
    };

    /* ------------------------------------------------------------- zoom */
    /* Deux gestes :
       - souris : clic maintenu puis glissé de plus de 6 px écran ouvre un
         rectangle de sélection. Un axe n'est zoomé que si le glissé le
         dépasse sur cet axe (glissé horizontal = zoom sur le temps seul).
       - tactile : deux doigts qu'on écarte (zoom) ou qu'on rapproche
         (dézoom), axe par axe selon l'écartement des doigts en X et en Y.
       Dans les deux cas la vue précédente est empilée → "Zoom précédent". */
    const DRAG_PX = 6;        // seuil de glissé, en pixels écran
    const PINCH_MIN = 30;     // écartement mini (unités viewBox) pour piloter un axe

    const pushView = v => {
      if (!(v.t1 - v.t0 > 0) || !(v.vmax - v.vmin > 0)) return;
      stack.push(v); live = null; render(); syncCtl();
    };
    const syncCtl = () => {
      ctl.hidden = !stack.length;
      if (opts.onZoom) opts.onZoom(stack.length ? Object.assign({}, vw) : null);
    };

    const toSvg = e => {
      const r = svg.getBoundingClientRect();
      const sx = r.width ? W / r.width : 1, sy = r.height ? H / r.height : 1;
      return { x: (e.clientX - r.left) * sx, y: (e.clientY - r.top) * sy,
               cx: e.clientX, cy: e.clientY };
    };
    const clampX = x => Math.max(M.l, Math.min(W - M.r, x));
    const clampY = y => Math.max(M.t, Math.min(H - M.b, y));

    let drag = null;          // glissé souris en cours
    let sel = null;           // rectangle de sélection affiché
    const touches = new Map();
    let pinch = null;         // état du pincement en cours

    function drawSel() {
      if (!sel) {
        sel = el("rect", { class: "zoom-sel", fill: "#e8a13c", "fill-opacity": .12,
          stroke: "#e8a13c", "stroke-width": .8, "stroke-dasharray": "4,3" });
        svg.appendChild(sel);
      }
      // Un axe non retenu (glissé trop court) est montré pleine hauteur /
      // pleine largeur : l'utilisateur voit tout de suite ce qui sera zoomé.
      const x = drag.axX ? [Math.min(drag.x0, drag.x), Math.max(drag.x0, drag.x)] : [M.l, W - M.r];
      const y = drag.axY ? [Math.min(drag.y0, drag.y), Math.max(drag.y0, drag.y)] : [M.t, H - M.b];
      sel.setAttribute("x", x[0]); sel.setAttribute("width", Math.max(0, x[1] - x[0]));
      sel.setAttribute("y", y[0]); sel.setAttribute("height", Math.max(0, y[1] - y[0]));
    }
    const dropSel = () => { if (sel) sel.remove(); sel = null; };

    function endDrag(apply) {
      if (!drag) return;
      const d = drag; drag = null;
      dropSel();
      if (!apply || !(d.axX || d.axY)) return;
      const nv = Object.assign({}, vw);
      if (d.axX) { nv.t0 = invX(Math.min(d.x0, d.x)); nv.t1 = invX(Math.max(d.x0, d.x)); }
      if (d.axY) { nv.vmax = invY(Math.min(d.y0, d.y)); nv.vmin = invY(Math.max(d.y0, d.y)); }
      pushView(nv);
    }

    function pinchSpread() {
      const [a, b] = [...touches.values()];
      return { dx: Math.abs(a.x - b.x), dy: Math.abs(a.y - b.y),
               mx: clampX((a.x + b.x) / 2), my: clampY((a.y + b.y) / 2) };
    }
    function startPinch() {
      const s = pinchSpread();
      // aT/aV : la donnée visée par le milieu des doigts au départ. Elle
      // restera collée au milieu des doigts pendant tout le geste.
      pinch = { s, view: Object.assign({}, vw), aT: invX(s.mx), aV: invY(s.my) };
      removeCursor(); dropSel(); drag = null;
    }
    /* Le milieu des doigts se déplace presque toujours — surtout quand les
       deux doigts partent du même côté du cadre : écarter, là, c'est autant
       translater qu'écarter. On garde donc la donnée visée au départ sous le
       milieu **courant** des doigts : le contenu suit les doigts (zoom +
       décalage), au lieu de rester ancré au point de départ. Un axe dont les
       doigts sont trop rapprochés (< PINCH_MIN) n'est pas mis à l'échelle
       (k = 1) mais se décale quand même avec eux. */
    function movePinch() {
      const s = pinchSpread(), pv = pinch.view;
      const nv = Object.assign({}, pv);
      const f = (d0, d1) => Math.max(.02, Math.min(50, d0 / Math.max(1, d1)));
      const kx = pinch.s.dx > PINCH_MIN ? f(pinch.s.dx, s.dx) : 1;
      const ky = pinch.s.dy > PINCH_MIN ? f(pinch.s.dy, s.dy) : 1;
      const spanT = (pv.t1 - pv.t0) * kx, spanV = (pv.vmax - pv.vmin) * ky;
      // Position relative du milieu des doigts dans le cadre : la donnée
      // ancrée doit tomber exactement là dans la nouvelle vue.
      const fx = (s.mx - M.l) / plotW, fy = (H - M.b - s.my) / plotH;
      [nv.t0, nv.t1] = clampAxis(pinch.aT - fx * spanT,
                                 pinch.aT + (1 - fx) * spanT, full.t0, full.t1);
      [nv.vmin, nv.vmax] = clampAxis(pinch.aV - fy * spanV,
                                     pinch.aV + (1 - fy) * spanV, full.vmin, full.vmax);
      if (nv.t1 - nv.t0 > 0 && nv.vmax - nv.vmin > 0) { live = nv; schedule(); }
    }
    function endPinch() {
      const v = live; pinch = null; live = null;
      if (!v) { render(); return; }
      // Pincement revenu (ou presque) à la vue complète : on vide la pile
      // plutôt que d'empiler une vue équivalente à l'originale.
      const near = (v.t1 - v.t0) >= (full.t1 - full.t0) * .999 &&
                   (v.vmax - v.vmin) >= (full.vmax - full.vmin) * .999;
      if (near) { stack.length = 0; render(); syncCtl(); }
      else pushView(v);
    }

    // La capture garde le geste vivant quand le doigt/curseur sort du cadre.
    // Elle peut échouer (pointeur déjà relâché) : jamais au prix du geste.
    const capture = e => { try { svg.setPointerCapture(e.pointerId); } catch { /* sans capture */ } };

    svg.addEventListener("pointerdown", e => {
      if (e.pointerType === "mouse") {
        if (e.button !== 0 || touches.size) return;
        e.preventDefault();
        const p = toSvg(e);
        drag = { x0: clampX(p.x), y0: clampY(p.y), x: clampX(p.x), y: clampY(p.y),
                 cx: p.cx, cy: p.cy, axX: false, axY: false };
        capture(e);
        return;
      }
      touches.set(e.pointerId, toSvg(e));
      if (touches.size === 2) startPinch();
      else if (touches.size > 2) { pinch = null; live = null; render(); }
      capture(e);
    });

    svg.addEventListener("pointermove", e => {
      const p = toSvg(e);
      if (touches.has(e.pointerId)) {
        touches.set(e.pointerId, p);
        if (pinch && touches.size === 2) movePinch();
        else if (touches.size === 1) showCursor(clampX(p.x));
        return;
      }
      if (touches.size) return;                 // doigts posés : pas de curseur
      if (drag) {
        drag.x = clampX(p.x); drag.y = clampY(p.y);
        if (Math.abs(p.cx - drag.cx) > DRAG_PX) drag.axX = true;
        if (Math.abs(p.cy - drag.cy) > DRAG_PX) drag.axY = true;
        if (drag.axX || drag.axY) { removeCursor(); drawSel(); return; }
      }
      showCursor(clampX(p.x));
    });

    const up = e => {
      if (touches.has(e.pointerId)) {
        touches.delete(e.pointerId);
        if (pinch && touches.size < 2) endPinch();
        if (!touches.size) removeCursor();
        return;
      }
      endDrag(e.type === "pointerup");
    };
    svg.addEventListener("pointerup", up);
    svg.addEventListener("pointercancel", up);
    svg.addEventListener("pointerleave", e => {
      if (e.pointerType === "mouse" && !drag) removeCursor();
    });
    // Un pincement ne doit pas se transformer en zoom du navigateur (le
    // touch-action:none de .chart-svg couvre déjà le défilement, pas tous les
    // gestes multi-doigts selon les navigateurs).
    svg.addEventListener("touchmove", e => {
      if (e.touches.length > 1) e.preventDefault();
    }, { passive: false });

    container.appendChild(svg);

    /* ---------------------------------------------- légende + commandes */
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = band
      ? `<span><i style="background:#e05b4f"></i>max</span>
         <span><i style="background:#e8a13c"></i>moyenne</span>
         <span><i style="background:#4f9de0"></i>min</span>
         <span class="muted">${data.mode === "daily" ? "pas journalier" : "pas horaire"}${opts.unit ? " · " + opts.unit : ""}</span>`
      : `<span><i style="background:#e8a13c"></i>valeur</span>
         <span class="muted">mesures brutes${opts.unit ? " · " + opts.unit : ""}</span>`;
    const ctl = document.createElement("span");
    ctl.className = "zoom-ctl";
    const mkBtn = (label, title, fn) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.title = title;
      b.onclick = fn;
      ctl.appendChild(b);
      return b;
    };
    mkBtn("↶ Zoom précédent", "Revenir à l'échelle précédente", () => {
      stack.pop(); live = null; render(); syncCtl();
    });
    mkBtn("⤢ Zoom initial", "Revenir à l'échelle d'origine", () => {
      stack.length = 0; live = null; render(); syncCtl();
    });
    legend.appendChild(ctl);
    container.appendChild(legend);

    render();
    ctl.hidden = !stack.length;
  };
})();
