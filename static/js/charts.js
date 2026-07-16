/* DomoPi charts — rendu SVG sans dépendance.
   Mode "raw"   : une courbe (pas 5 min).
   Mode "hourly"/"daily" : courbe moyenne + bande min-max (3 courbes). */
(function () {
  const NS = "http://www.w3.org/2000/svg";

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

  function fmtTime(ts, spanS) {
    const d = new Date(ts * 1000);
    if (spanS <= 2 * 86400) return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    if (spanS <= 40 * 86400) return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
    return d.toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
  }

  /* container: élément DOM ; data: {mode, points} ; opts: {unit, height} */
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
    vmin -= pad; vmax += pad;
    const X = t => M.l + (t - t0) / Math.max(1, t1 - t0) * (W - M.l - M.r);
    const Y = v => H - M.b - (v - vmin) / (vmax - vmin) * (H - M.t - M.b);

    for (const v of niceTicks(vmin, vmax, 5)) {
      svg.appendChild(el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v),
        stroke: "#2b3442", "stroke-width": .6 }));
      const t = el("text", { x: M.l - 6, y: Y(v) + 3.5, fill: "#8b97a5",
        "font-size": 10, "text-anchor": "end", "font-family": "monospace" });
      t.textContent = Math.abs(v) >= 1000 ? v.toFixed(0) : +v.toFixed(2);
      svg.appendChild(t);
    }
    const nx = 5;
    for (let i = 0; i <= nx; i++) {
      const ts = t0 + (t1 - t0) * i / nx;
      const t = el("text", { x: X(ts), y: H - 7, fill: "#8b97a5",
        "font-size": 10, "text-anchor": i === 0 ? "start" : i === nx ? "end" : "middle" });
      t.textContent = fmtTime(ts, t1 - t0);
      svg.appendChild(t);
    }

    const line = key => pts.map((p, i) =>
      (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(band ? p[key] : p.v).toFixed(1)).join("");

    if (band) {
      const up = pts.map((p, i) => (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(p.max).toFixed(1)).join("");
      const down = pts.slice().reverse().map(p => "L" + X(p.t).toFixed(1) + " " + Y(p.min).toFixed(1)).join("");
      svg.appendChild(el("path", { d: up + down + "Z", fill: "#e8a13c", "fill-opacity": .14, stroke: "none" }));
      svg.appendChild(el("path", { d: line("max"), fill: "none", stroke: "#e05b4f", "stroke-width": 1 }));
      svg.appendChild(el("path", { d: line("min"), fill: "none", stroke: "#4f9de0", "stroke-width": 1 }));
      svg.appendChild(el("path", { d: line("avg"), fill: "none", stroke: "#e8a13c", "stroke-width": 1.8 }));
    } else {
      svg.appendChild(el("path", { d: line("v"), fill: "none", stroke: "#e8a13c",
        "stroke-width": 1.6, "stroke-linejoin": "round" }));
    }

    /* Curseur au survol : ligne verticale accrochée au point réel le plus
       proche, avec étiquette date (+ heure en mode "raw", <= 4 j) et
       valeur(s) courante(s). Position verticale de l'étiquette fixe (haut du
       graphe) — seule l'abscisse suit la souris. */
    const fmtVal = v => (Math.abs(v) >= 1000 ? v.toFixed(0) : +v.toFixed(2)) +
      (opts.unit ? " " + opts.unit : "");
    const fmtCursorDate = (ts, withTime) => {
      const d = new Date(ts * 1000);
      const day = d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric" });
      return withTime ? day + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" }) : day;
    };
    const nearestIdx = t => {
      let lo = 0, hi = pts.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid].t < t) lo = mid + 1; else hi = mid; }
      if (lo > 0 && Math.abs(pts[lo - 1].t - t) <= Math.abs(pts[lo].t - t)) lo--;
      return lo;
    };
    const removeCursor = () => svg.querySelectorAll(".cursor-g").forEach(n => n.remove());
    const showCursor = px => {
      removeCursor();
      const t = t0 + (px - M.l) / Math.max(1, W - M.l - M.r) * (t1 - t0);
      const p = pts[nearestIdx(t)];
      const cx = X(p.t);
      const g = el("g", { class: "cursor-g" });
      g.appendChild(el("line", { x1: cx, x2: cx, y1: M.t, y2: H - M.b,
        stroke: "#8b97a5", "stroke-width": .8, "stroke-dasharray": "3,2" }));
      const lines = [{ text: fmtCursorDate(p.t, !band), color: "#c8d0d8" }];
      if (band) {
        g.appendChild(el("circle", { cx, cy: Y(p.max), r: 2.6, fill: "#e05b4f" }));
        g.appendChild(el("circle", { cx, cy: Y(p.avg), r: 2.6, fill: "#e8a13c" }));
        g.appendChild(el("circle", { cx, cy: Y(p.min), r: 2.6, fill: "#4f9de0" }));
        lines.push({ text: "max " + fmtVal(p.max), color: "#e05b4f" });
        lines.push({ text: "moy " + fmtVal(p.avg), color: "#e8a13c" });
        lines.push({ text: "min " + fmtVal(p.min), color: "#4f9de0" });
      } else {
        g.appendChild(el("circle", { cx, cy: Y(p.v), r: 2.8, fill: "#e8a13c" }));
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
    svg.addEventListener("pointermove", e => {
      const r = svg.getBoundingClientRect();
      if (!r.width) return;
      const px = Math.max(M.l, Math.min(W - M.r, (e.clientX - r.left) / r.width * W));
      showCursor(px);
    });
    svg.addEventListener("pointerleave", removeCursor);

    container.appendChild(svg);

    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = band
      ? `<span><i style="background:#e05b4f"></i>max</span>
         <span><i style="background:#e8a13c"></i>moyenne</span>
         <span><i style="background:#4f9de0"></i>min</span>
         <span class="muted">${data.mode === "daily" ? "pas journalier" : "pas horaire"}${opts.unit ? " · " + opts.unit : ""}</span>`
      : `<span><i style="background:#e8a13c"></i>valeur</span>
         <span class="muted">pas 5 min${opts.unit ? " · " + opts.unit : ""}</span>`;
    container.appendChild(legend);
  };
})();
