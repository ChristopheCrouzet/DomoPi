/* DomoPi — tuiles et graphes de périphérique, partagés par le visualiseur
   (app.js) et l'administration (admin.js, dialogue « Tester et exporter »).

   Une seule implémentation pour : format des valeurs, icônes on/off et état
   partiel, niveau de consigne, dialogue de réglage sur échelle, graphe avec
   ses boutons de plage. Dupliquer ce code entre les deux écrans revenait à
   maintenir deux fois des règles d'interaction fines (clic court/long,
   double-clic, tempo d'auto-validation).

   L'hôte fournit son contexte par configure() : son `api` (le visualiseur
   renvoie au login sur 401, l'admin vers la page d'accueil), son `toast`, le
   <dialog> à utiliser, et `onAck` (relecture du périphérique après une
   commande — chaque écran rafraîchit son propre affichage). */
window.DomoTile = (function () {
  /* Durées des graphes : mêmes défauts que le serveur (db.py:DEFAULT_SETTINGS)
     et que l'admin (admin.js:DEFAULT_RANGES) — garder les trois cohérents. */
  const DEFAULT_RANGES = [
    { label: "24 h", span_s: 86400, mode: "raw" },
    { label: "4 j", span_s: 345600, mode: "raw" },
    { label: "15 j", span_s: 1296000, mode: "minmax" },
    { label: "30 j", span_s: 2592000, mode: "minmax" },
    { label: "90 j", span_s: 7776000, mode: "minmax" },
    { label: "6 mois", span_s: 15724800, mode: "minmax" }];

  let cfg = {
    api: () => Promise.reject(new Error("DomoTile non configuré")),
    toast: () => {},
    dlg: null, body: null,          // <dialog> et son conteneur de contenu
    onAck: () => {},                // (id) après une commande réussie
    mobile: () => window.innerWidth < 700,
  };
  let disp = { sig: 5, thou: " ", dec: "," };
  let ranges = DEFAULT_RANGES;

  const configure = o => { cfg = Object.assign(cfg, o); };
  const setDisplay = d => { disp = Object.assign({}, disp, d); };
  const setRanges = r => { ranges = Array.isArray(r) && r.length ? r : DEFAULT_RANGES; };
  const getRanges = () => ranges;

  const isOn = v => {
    if (v == null) return false;
    const s = String(v).toLowerCase();
    if (["on", "1", "true", "open", "ouvert", "100"].includes(s)) return true;
    const n = parseFloat(s); return !isNaN(n) && n > 0;
  };

  /* Format d'une valeur numérique : au plus `disp.sig` chiffres significatifs
     (défaut 5) et 2 décimales, partie entière toujours complète (jamais de
     notation ingénieur), séparateurs de milliers et décimal configurables
     (Réglages généraux → Affichage). `stepDec` (décimales du pas d'une
     échelle : 10 -> 0, 0.5 -> 1) fixe le nombre de décimales, borné par la
     règle ci-dessus ; sans échelle, les zéros finaux sont retirés. */
  function fmtNum(v, stepDec) {
    const n = typeof v === "number" ? v : parseFloat(String(v).replace(",", "."));
    if (!isFinite(n)) return String(v ?? "—");
    const ints = Math.max(1, Math.floor(Math.log10(Math.abs(n))) + 1);
    const cap = Math.max(0, Math.min(2, disp.sig - ints));
    let s = Math.abs(n).toFixed(stepDec == null ? cap : Math.min(stepDec, cap));
    if (stepDec == null) s = String(+s);
    const [ip, fp] = s.split(".");
    return (n < 0 ? "-" : "") + ip.replace(/\B(?=(\d{3})+$)/g, disp.thou) +
           (fp ? disp.dec + fp : "");
  }
  window.fmtNum = fmtNum;   // utilisé par charts.js (axes et infobulles)
  /* Format d'une valeur au pas de l'échelle (10 -> "20", 0.1 -> "19,5"). */
  const fmtScale = (v, step) =>
    fmtNum(+v, (String(step ?? 1).split(".")[1] || "").length);

  const ack = id => { setTimeout(() => cfg.onAck(id), 300);
                      setTimeout(() => cfg.onAck(id), 2000); };

  /* ------------------------------------------------ tuile périphérique */
  /* opts : {label} texte affiché, {wid} identifiant de widget porté par
     data-wid (rafraîchissement ciblé), {onChart} action au clic pour un
     capteur non pilotable (défaut : ouvrir le graphe). */
  function card(d, opts) {
    d = d || {}; opts = opts || {};
    const isActuator = d.kind === "actuator";
    // Échelle aussi sur un capteur virtuel pilotable (consigne, mode...) —
    // et son affichage (icône/texte des valeurs) vaut même sans pilotage.
    const sc = d.scale || null;
    const num = parseFloat(String(d.last_value ?? "").replace(",", "."));
    // Position 0-100 % de la valeur sur l'échelle de pilotage (gradateur,
    // ouverture de volet, consigne de chauffage...)
    const pct = sc && !isNaN(num)
      ? Math.max(0, Math.min(100, (num - sc.vmin) / (sc.vmax - sc.vmin) * 100)) : null;
    // Valeur de la série correspondant à la valeur courante (au demi-cran
    // près) : son icône et son texte remplacent alors ceux du périphérique.
    const stop = sc && !isNaN(num)
      ? (sc.stops || []).find(s => Math.abs(num - s.value) <= (sc.step || 1) / 2) : null;
    const on = pct != null ? pct > 0 : isOn(d.last_value);
    const dim = isActuator && !on;
    const stale = d.last_seen && (Date.now() / 1000 - d.last_seen > 3 * 3600);
    const c = document.createElement("div");
    c.className = "card" + (d.controllable ? " clickable" : "");
    if (opts.wid != null) c.dataset.wid = opts.wid;
    // Tuile pilotable sur échelle (avec barre) : la tuile s'éclaircit depuis
    // le bas à hauteur de la consigne — sombre au mini, claire au maxi.
    if (sc && d.controllable && !sc.hide_slider && pct != null) {
      c.classList.add("lvl");
      c.style.setProperty("--lvl", pct.toFixed(1) + "%");
    }

    // Icône : pour un état partiel (0 < pct < 100), l'icône "on" est révélée
    // depuis le bas à hauteur du pourcentage, superposée à l'icône "off".
    let iconHtml = "";
    if (stop && stop.icon) {
      iconHtml = `<div class="icon"><img src="/static/icons/${stop.icon}" alt=""></div>`;
    } else if (sc && pct != null && pct > 0 && pct < 100 && d.icon_on && d.icon_off) {
      iconHtml = `<div class="icon"><span class="stack" style="--pct:${pct}%">
        <img src="/static/icons/${d.icon_off}" alt="">
        <img class="top" src="/static/icons/${d.icon_on}" alt=""></span></div>`;
    } else {
      // Les icônes basculent aussi pour un capteur virtuel pilotable
      // (mode on/off eedomus) — un capteur non pilotable garde son icône fixe.
      const icon = isActuator || d.controllable
        ? (on ? (d.icon_on || d.icon_off) : (d.icon_off || d.icon_on))
        : (d.icon_on || d.icon_off);
      if (icon) iconHtml = `<div class="icon"><img src="/static/icons/${icon}" alt=""></div>`;
    }
    // Sans échelle, seule une valeur purement numérique est reformatée
    // (les états texte « on », « Ouvert »... restent tels quels).
    // « NaN » = capteur calculé incalculable (division par zéro...) : invalide.
    const invalid = /^nan$/i.test(String(d.last_value ?? "").trim());
    const valueHtml = invalid ? `<span class="invalid">invalide</span>`
      : sc && !isNaN(num)
      ? (stop && stop.label ? stop.label
         : fmtScale(num, sc.step) + (d.unit ? " " + d.unit : ""))
      : `${/^\s*-?\d+([.,]\d+)?\s*$/.test(String(d.last_value ?? ""))
           ? fmtNum(num) : d.last_value ?? "—"}${d.unit ? " " + d.unit : ""}`;
    c.innerHTML = iconHtml +
      `<div class="value ${dim ? "off" : ""}">${valueHtml}</div>
       <div class="name">${opts.label || d.name || "?"}</div>` +
      (stale ? `<div class="stale">sans réponse</div>` : "");
    if (d.controllable) {
      c.tabIndex = 0;
      const toggle = async () => {
        const target = on ? "off" : "on";
        try {
          await cfg.api(`/api/devices/${d.id}/set`, { method: "POST",
            body: JSON.stringify({ value: target }) });
          cfg.toast(`${d.name} → ${target === "on" ? "marche" : "arrêt"}`);
          ack(d.id);
        } catch (e) { cfg.toast("Échec : " + e.message); }
      };
      if (sc) {
        const openDetails = () => openScale(d, sc, !isNaN(num) ? num : sc.vmin);
        if (!sc.toggle_click) {
          // Échelle « consigne » (chauffage, mode...) : le clic ouvre le réglage.
          c.title = "Cliquer pour régler";
          c.onclick = openDetails;
          c.onkeydown = e => { if (e.key === "Enter") openDetails(); };
          return c;
        }
        // Clic/appui court : marche-arrêt. Double-clic (PC) ou appui long
        // (mobile, avec micro-vibration et zoom du cadre) : réglage sur l'échelle.
        c.title = "Clic : marche/arrêt · double-clic ou appui long : réglage";
        let pressTimer = null, longFired = false, clickTimer = null;
        c.onpointerdown = e => {
          if (e.pointerType === "mouse" && e.button !== 0) return;
          longFired = false;
          pressTimer = setTimeout(() => {
            longFired = true;
            if (navigator.vibrate) navigator.vibrate(30);
            c.classList.add("pressing");
            setTimeout(() => c.classList.remove("pressing"), 350);
            openDetails();
          }, 500);
        };
        const cancelPress = () => clearTimeout(pressTimer);
        c.onpointerup = cancelPress; c.onpointercancel = cancelPress;
        c.onpointerleave = cancelPress;
        c.oncontextmenu = e => e.preventDefault();
        c.onclick = () => {
          if (longFired) { longFired = false; return; }
          if (clickTimer) return;                 // 2e clic d'un double-clic
          clickTimer = setTimeout(() => { clickTimer = null; toggle(); }, 280);
        };
        c.ondblclick = () => { clearTimeout(clickTimer); clickTimer = null; openDetails(); };
        c.onkeydown = e => { if (e.key === "Enter") toggle(); };
      } else {
        c.title = "Cliquer pour basculer";
        c.onclick = toggle;
        c.onkeydown = e => { if (e.key === "Enter") toggle(); };
      }
    } else {
      // Non pilotable : le clic montre l'historique. La condition portait sur
      // `kind === "sensor"`, mais un périphérique virtuel sans formule est
      // désormais typé « sortie » (27/07/2026) — non pilotable, sa tuile
      // serait devenue morte. Tout ce qui ne se pilote pas ouvre son graphe.
      c.classList.add("clickable");
      c.onclick = opts.onChart || (() => openChart(d));
    }
    return c;
  }

  /* Réglage sur échelle : curseur borné/cranté (optionnel) + boutons de la
     série de valeurs (texte et icône optionnels). Gradateurs, volets,
     consignes de chauffage, modes... */
  function openScale(d, sc, current) {
    const dlg = cfg.dlg, body = cfg.body;
    const unit = d.unit ? " " + d.unit : "";
    const fmt = v => fmtScale(v, sc.step) + unit;
    const snap = v => {
      v = Math.max(sc.vmin, Math.min(sc.vmax, +v || 0));
      return Math.round((v - sc.vmin) / sc.step) * sc.step + sc.vmin;
    };
    current = snap(current);
    const delayS = sc.send_delay_s == null ? 1.5 : Math.max(0, +sc.send_delay_s);
    const stops = sc.stops || [];
    body.innerHTML = `<button class="dlg-x" id="dim-close" title="Fermer">✕</button>
      <h2 style="margin-top:0">${d.name}</h2>
      <div class="value" id="dim-val" style="text-align:center;font-size:1.6rem;
           font-family:var(--mono);margin:.2rem 0 .5rem">${fmt(current)}</div>
      ${sc.hide_slider ? "" : `
      <input type="range" id="dim-slider" min="${sc.vmin}" max="${sc.vmax}"
             step="${sc.step}" value="${current}"
             style="width:100%;height:2.4rem;touch-action:none">
      <div style="display:flex;justify-content:space-between" class="muted">
        <span>${fmt(sc.vmin)}</span><span>${fmt(sc.vmax)}</span></div>`}
      ${stops.length ? `<div class="scale-btns">${stops.map(s =>
        `<button class="btn" data-v="${s.value}" title="${fmt(s.value)}">
           ${s.icon ? `<img src="/static/icons/${s.icon}" alt="">` : ""}
           <span>${s.label || fmtScale(s.value, sc.step)}</span></button>`).join("")}
      </div>` : ""}
      ${sc.hide_slider || !delayS ? "" : `<p class="muted"
        style="margin:.6rem 0 0;font-size:.8rem">La consigne part automatiquement
        ${String(delayS).replace(".", ",")}&nbsp;s après le relâchement du curseur.</p>`}`;
    const slider = body.querySelector("#dim-slider");
    const valEl = body.querySelector("#dim-val");
    let sendTimer = null;
    const send = async v => {
      clearTimeout(sendTimer); sendTimer = null;
      try {
        await cfg.api(`/api/devices/${d.id}/set`, { method: "POST",
          body: JSON.stringify({ value: String(v) }) });
        cfg.toast(`${d.name} → ${fmt(v)}`);
        dlg.close();
        ack(d.id);
      } catch (e) { cfg.toast("Échec : " + e.message); }
    };
    if (slider) {
      slider.oninput = () => {            // en cours de glissement : annule l'envoi
        clearTimeout(sendTimer); sendTimer = null;
        valEl.textContent = fmt(slider.value);
      };
      slider.onchange = () => {           // relâchement : envoi (différé si tempo)
        clearTimeout(sendTimer);
        if (!delayS) return send(slider.value);
        valEl.textContent = fmt(slider.value) + " …";
        sendTimer = setTimeout(() => send(slider.value), delayS * 1000);
      };
    }
    body.querySelectorAll("[data-v]").forEach(b =>
      b.onclick = () => { if (slider) slider.value = b.dataset.v;
                          valEl.textContent = fmt(b.dataset.v);
                          send(b.dataset.v); });
    body.querySelector("#dim-close").onclick = () => { clearTimeout(sendTimer); dlg.close(); };
    dlg.showModal();
  }

  /* ------------------------------------------------ graphe */
  const rangeMode = span =>
    (ranges.find(r => +r.span_s === span) || {}).mode || "auto";

  /* Retourne {box, ready} : ready résout une fois les données de la plage
     initiale chargées — permet à l'appelant (renderPage) d'attendre tous les
     graphes d'une page avant de les afficher, pour éviter un DOM "vide" qui
     s'effondre puis se remplit graphe par graphe.
     opts : {label, span, zoom, wid, height}. */
  function chart(d, opts) {
    d = d || {}; opts = opts || {};
    const box = document.createElement("div");
    box.className = "chart-box wide";
    if (opts.wid != null) box.dataset.wid = opts.wid;
    // Zoom en cours (voir charts.js) : porté par la boîte pour que la MAJ
    // "soft" le retrouve et le repose sur le graphe reconstruit.
    box._zoom = opts.zoom || null;
    const head = document.createElement("div");
    head.className = "chart-head";
    head.innerHTML = `<span class="title">${opts.label || d.name || ""}
      <span class="muted mono" style="font-family:var(--mono)">${d.last_value ?? ""}${d.unit ? " " + d.unit : ""}</span></span>`;
    // Cellule dédiée aux boutons de plage : ils se replient dedans, alignés à
    // droite, face au titre (lui-même sur 1 ou 2 lignes) — au lieu de passer
    // sous le titre et de manger la hauteur du graphe sur mobile.
    const rangeBox = document.createElement("span");
    rangeBox.className = "ranges";
    head.appendChild(rangeBox);
    const plot = document.createElement("div");
    const def = opts.span || 86400;
    const load = async (span, btn, keepZoom) => {
      head.querySelectorAll("button").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      if (!keepZoom) box._zoom = null;      // changer de plage annule le zoom
      const now = Math.floor(Date.now() / 1000);
      try {
        const data = await cfg.api(`/api/series/${d.id}?t_from=${now - span}&t_to=${now}` +
                                   `&mode=${rangeMode(span)}`);
        renderChart(plot, data, { unit: d.unit,
                                  height: opts.height || (cfg.mobile() ? 200 : 260),
                                  view: box._zoom, onZoom: v => { box._zoom = v; } });
      } catch (e) { plot.innerHTML = `<p class="muted">${e.message}</p>`; }
    };
    let activeBtn = null;
    for (const r of ranges) {
      const b = document.createElement("button");
      b.textContent = r.label;
      b.dataset.span = r.span_s;
      if (+r.span_s === def) activeBtn = b;
      b.onclick = () => load(+r.span_s, b);
      rangeBox.appendChild(b);
    }
    // Fenêtre mémorisée/du widget absente des durées paramétrées : 1er bouton.
    if (!activeBtn) activeBtn = head.querySelector("button");
    box.appendChild(head); box.appendChild(plot);
    return { box, ready: load(+activeBtn.dataset.span, activeBtn, true) };
  }

  /* Graphe seul, en dialogue (clic sur la tuile d'un capteur). */
  function openChart(d) {
    const dlg = cfg.dlg, body = cfg.body;
    body.innerHTML = "";
    const x = document.createElement("button");
    x.className = "dlg-x"; x.textContent = "✕"; x.title = "Fermer";
    x.onclick = () => dlg.close();
    body.appendChild(x);
    body.appendChild(chart(d, { label: d.name, span: 86400 }).box);
    dlg.showModal();
  }

  return { configure, setDisplay, setRanges, getRanges, DEFAULT_RANGES,
           isOn, fmtNum, fmtScale, card, openScale, chart, openChart };
})();
