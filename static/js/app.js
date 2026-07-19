/* DomoPi — visualiseur (pages arborescentes, widgets, journal). */
(function () {
  const $ = s => document.querySelector(s);
  let me = null, pages = [], devices = {}, currentPage = null, refreshTimer = null;
  let currentWidgets = [], liveTimers = [];
  let disp = { sig: 5, thou: " ", dec: "," };   // réglages d'affichage (serveur)

  const api = async (url, opts) => {
    const r = await fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    if (r.status === 401 && !url.endsWith("/me")) { showLogin(); throw new Error("401"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  };
  const toast = msg => {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  };
  const isMobile = () => window.innerWidth < 700;
  const isOn = v => {
    if (v == null) return false;
    const s = String(v).toLowerCase();
    if (["on", "1", "true", "open", "ouvert", "100"].includes(s)) return true;
    const n = parseFloat(s); return !isNaN(n) && n > 0;
  };

  /* ------------------------------------------------ session */
  function showLogin() {
    clearInterval(refreshTimer); liveTimers.forEach(clearInterval); liveTimers = [];
    $("#banner").hidden = true; $("#main").hidden = true; $("#login-view").hidden = false;
    $("#page-bg").style.background = "";
  }
  async function boot() {
    try { me = await api("/api/me"); } catch { me = null; }
    if (!me || !me.username) return showLogin();
    $("#login-view").hidden = true; $("#banner").hidden = false; $("#main").hidden = false;
    $("#who").textContent = me.username + (me.role === "admin" ? " (admin)" : "");
    $("#btn-admin").hidden = me.role !== "admin";
    await loadAll();
    const hash = location.hash.slice(1);
    if (hash === "journal") renderJournal();
    else openPage(parseInt(hash) || rootPages()[0]?.id || null);
    refreshTimer = setInterval(refreshValues, 60000);
  }

  $("#li-go").onclick = async () => {
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({
        username: $("#li-user").value, password: $("#li-pass").value }) });
      $("#li-err").hidden = true; boot();
    } catch { $("#li-err").hidden = false; }
  };
  $("#li-pass").addEventListener("keydown", e => { if (e.key === "Enter") $("#li-go").click(); });
  $("#btn-logout").onclick = async () => { await api("/api/logout", { method: "POST" }); showLogin(); };
  $("#btn-admin").onclick = () => location.href = "/static/admin.html";
  $("#btn-journal").onclick = () => { location.hash = "journal"; renderJournal(); };

  /* ------------------------------------------------ données */
  async function loadAll() {
    const [pg, devs, dp] = await Promise.all(
      [api("/api/pages"), api("/api/devices"), api("/api/display")]);
    pages = pg; devs.forEach(d => devices[d.id] = d);
    disp = { sig: Math.max(3, parseInt(dp.display_sig_digits, 10) || 5),
             thou: dp.display_thousands_sep ?? " ",
             dec: dp.display_decimal_sep || "," };
    try {
      const cr = JSON.parse(dp.chart_ranges);
      if (Array.isArray(cr) && cr.length) chartRanges = cr;
    } catch { /* réglage absent : défauts */ }
    buildRootNav();
  }
  async function refreshValues() {
    try { (await api("/api/devices")).forEach(d => devices[d.id] = d); } catch { return; }
    if (currentPage != null) renderPage(currentPage, true);
  }
  const rootPages = () => pages.filter(p => p.parent_id == null);
  const childPages = id => pages.filter(p => p.parent_id === id);
  const pageById = id => pages.find(p => p.id === id);

  function buildRootNav() {
    const nav = $("#root-nav"); nav.innerHTML = "";
    for (const p of rootPages()) {
      const a = document.createElement("a");
      a.href = "#" + p.id;
      if (p.icon) {
        const im = document.createElement("img");
        im.className = "navicon"; im.alt = "";
        im.src = "/static/icons/" + p.icon;
        a.appendChild(im);
      }
      a.appendChild(document.createTextNode(p.title));
      a.onclick = e => { e.preventDefault(); openPage(p.id); };
      nav.appendChild(a);
    }
  }

  /* ------------------------------------------------ pages */
  async function openPage(id) {
    currentPage = id; location.hash = id ?? "";
    renderPage(id, false);
  }

  async function renderPage(id, soft) {
    const main = $("#main");
    // Une MAJ "soft" (cycle 60 s) recrée tout le DOM de la page : on
    // mémorise ici le choix de plage (1J/4J/15J) de chaque graphe et le
    // défilement de la page pour les restaurer après coup, sinon l'utilisateur
    // les perd à chaque rafraîchissement périodique.
    const prevRanges = new Map();
    let prevScrollY = null;
    if (soft) {
      main.querySelectorAll(".chart-box[data-wid]").forEach(box => {
        const btn = box.querySelector(".chart-head button.active");
        if (btn) prevRanges.set(box.dataset.wid, +btn.dataset.span);
      });
      prevScrollY = window.scrollY;
    }
    document.querySelectorAll("#root-nav a").forEach(a =>
      a.classList.toggle("active", a.hash === "#" + id));
    const page = pageById(id);
    $("#page-bg").style.background = page && page.background
      ? (page.background.startsWith("#") || page.background.startsWith("rgb")
         ? page.background
         : `var(--bg) url('/static/backgrounds/${page.background}') center/cover fixed`)
      : "";
    if (!page) {
      main.innerHTML = "<h1>Bienvenue</h1><p class='muted'>Aucune page n'est encore définie. " +
        (me.role === "admin" ? "Créez vos pages dans <a href='/static/admin.html#pages'>Paramètres</a>." :
         "Demandez à l'administrateur de créer des pages.") + "</p>";
      return;
    }
    if (!soft) main.innerHTML = "";
    /* fil d'Ariane : uniquement les pages parentes (le titre h1 affiche
       déjà la page courante — sinon le nom apparaît en double) */
    let crumbs = [], p = pageById(page.parent_id);
    while (p) { crumbs.unshift(p); p = pageById(p.parent_id); }
    const crumbHtml = crumbs.length
      ? crumbs.map(c => `<a href="#${c.id}">${c.title}</a>`).join(" › ") + " ›"
      : "";

    let widgets = await api(`/api/pages/${id}/widgets`);
    if (page.dual_layout)
      widgets = widgets.filter(w => w.layout === "both" || w.layout === (isMobile() ? "mobile" : "desktop"));
    currentWidgets = widgets;

    const frag = document.createElement("div");
    frag.innerHTML = (crumbHtml ? `<div class="crumbs">${crumbHtml}</div>` : "") +
                     `<h1>${page.title}</h1>`;
    const grid = document.createElement("div"); grid.className = "grid";
    frag.appendChild(grid);

    // Tuiles de sous-pages et widgets partagent le même ordre d'affichage
    // (à ordre égal, les sous-pages passent en premier).
    const items = [
      ...childPages(id).map(k => ({ sort: k.sort_order || 0, kid: k })),
      ...widgets.map(w => ({ sort: w.sort_order || 0, w })),
    ].sort((a, b) => a.sort - b.sort);
    const graphReady = [];
    for (const it of items) {
      if (it.kid) {
        const k = it.kid;
        grid.appendChild(linkCard(k.title, k.icon, () => openPage(k.id)));
        continue;
      }
      const w = it.w;
      if (w.wtype === "pagelink" && w.target_page_id)
        grid.appendChild(linkCard(w.options.label || w.target_title || "Page",
                                  w.target_icon || "",
                                  () => openPage(w.target_page_id)));
      else if (w.wtype === "label") {
        const c = document.createElement("div");
        c.className = "card wide"; c.style.minHeight = "0";
        c.innerHTML = `<div class="name" style="text-align:left;font-size:.95rem;color:var(--txt)">${w.options.text || ""}</div>`;
        grid.appendChild(c);
      } else if (w.wtype === "graph" && w.device_id) {
        const gw = graphWidget(w, prevRanges.get(String(w.id)));
        grid.appendChild(gw.box);
        graphReady.push(gw.ready);
      } else if (w.device_id)
        grid.appendChild(deviceCard(w));
    }
    // MAJ "soft" : on attend que tous les graphes aient chargé leurs données
    // avant de basculer le DOM, sinon la page s'effondre (courbes vides) le
    // temps des requêtes puis "saute" en se remplissant — visible surtout
    // avec de nombreux graphes sur une même page.
    if (soft) await Promise.all(graphReady);
    main.innerHTML = ""; main.appendChild(frag);
    if (soft && prevScrollY != null) window.scrollTo(0, prevScrollY);
    if (!soft) setLive(widgets.filter(w => w.wtype === "device" && w.device_id)
                              .map(w => w.device_id));
  }

  /* Rafraîchissement rapide des widgets état+valeur affichés : interroge
     les connecteurs à la demande (sans historiser) et remplace uniquement
     les cartes concernées — les graphes ne sont pas re-rendus. La cadence
     vient du réglage live_refresh_s de chaque contrôleur (0 = désactivé,
     plancher 5 s) : un timer par cadence distincte. */
  function setLive(ids) {
    liveTimers.forEach(clearInterval); liveTimers = [];
    const groups = new Map();
    [...new Set(ids)].forEach(id => {
      const d = devices[id];
      if (!d) return;
      const s = d.live_refresh_s ?? 10;
      if (!s) return;                          // 0 = désactivé pour ce contrôleur
      const key = Math.max(5, s);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(id);
    });
    for (const [s, gids] of groups) {
      // Rafraîchit tout de suite : sans ça, last_seen d'un périphérique non
      // surveillé (mis à jour uniquement par ce tick) peut dater de la
      // dernière visite de la page, faisant clignoter le badge "sans réponse"
      // le temps du premier cycle (jusqu'à live_refresh_s, 10 s par défaut).
      liveTick(gids);
      liveTimers.push(setInterval(() => liveTick(gids), s * 1000));
    }
  }
  async function liveTick(ids) {
    if (document.hidden || currentPage == null) return;
    try {
      const rows = await api("/api/devices/refresh", { method: "POST",
        body: JSON.stringify({ ids }) });
      let changed = false;
      rows.forEach(r => {
        const d = devices[r.id];
        if (d && (d.last_value !== r.last_value || d.last_seen !== r.last_seen)) {
          d.last_value = r.last_value; d.last_seen = r.last_seen; changed = true;
        }
      });
      if (changed) refreshDeviceCards();
    } catch { /* silencieux : le cycle 60 s reprendra */ }
  }
  function refreshDeviceCards() {
    document.querySelectorAll(".card[data-wid]").forEach(el => {
      const w = currentWidgets.find(x => x.id === +el.dataset.wid);
      if (w) el.replaceWith(deviceCard(w));
    });
  }
  /* Acquittement d'une commande : relit rapidement le périphérique concerné
     (0,3 s puis 2 s) pour que l'icône reflète l'action sans attendre. */
  async function ackRefresh(id) {
    try {
      const rows = await api("/api/devices/refresh", { method: "POST",
        body: JSON.stringify({ ids: [id] }) });
      rows.forEach(r => {
        const d = devices[r.id];
        if (d) { d.last_value = r.last_value; d.last_seen = r.last_seen; }
      });
      refreshDeviceCards();
    } catch { /* silencieux */ }
  }
  const ack = id => { setTimeout(() => ackRefresh(id), 300);
                      setTimeout(() => ackRefresh(id), 2000); };

  function linkCard(label, icon, onClick) {
    const c = document.createElement("div");
    c.className = "card link clickable"; c.tabIndex = 0;
    // Dossier par défaut ; si une icône est associée à la page cible, elle
    // apparaît derrière le dossier, à 50 % d'opacité, centrée comme les tuiles.
    c.innerHTML = `<div class="lstack">${icon
        ? `<img src="/static/icons/${icon}" alt="">` : ""}<span class="folder">📁</span></div>
      <div class="name">${label}</div>`;
    c.onclick = onClick;
    c.onkeydown = e => { if (e.key === "Enter") onClick(); };
    return c;
  }

  /* ------------------------------------------------ widget périphérique */
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

  function deviceCard(w) {
    const d = devices[w.device_id] || {};
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
    if (w.id) c.dataset.wid = w.id;
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
         : fmtScale(num, sc.step) + (d.unit ? " " + d.unit : ""))
      : `${/^\s*-?\d+([.,]\d+)?\s*$/.test(String(d.last_value ?? ""))
           ? fmtNum(num) : d.last_value ?? "—"}${d.unit ? " " + d.unit : ""}`;
    c.innerHTML = iconHtml +
      `<div class="value ${dim ? "off" : ""}">${valueHtml}</div>
       <div class="name">${w.options.label || d.name || "?"}</div>` +
      (stale ? `<div class="stale">sans réponse</div>` : "");
    if (d.controllable) {
      c.tabIndex = 0;
      const toggle = async () => {
        const target = on ? "off" : "on";
        try {
          await api(`/api/devices/${d.id}/set`, { method: "POST",
            body: JSON.stringify({ value: target }) });
          toast(`${d.name} → ${target === "on" ? "marche" : "arrêt"}`);
          ack(d.id);
        } catch (e) { toast("Échec : " + e.message); }
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
    } else if (d.kind === "sensor") {
      c.classList.add("clickable");
      c.onclick = () => openZoom(d);
    }
    return c;
  }

  /* Réglage sur échelle : curseur borné/cranté (optionnel) + boutons de la
     série de valeurs (texte et icône optionnels). Gradateurs, volets,
     consignes de chauffage, modes... */
  function openScale(d, sc, current) {
    const dlg = $("#zoom-dlg"), body = $("#zoom-body");
    const unit = d.unit ? " " + d.unit : "";
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
    const slider = $("#dim-slider");
    let sendTimer = null;
    const send = async v => {
      clearTimeout(sendTimer); sendTimer = null;
      try {
        await api(`/api/devices/${d.id}/set`, { method: "POST",
          body: JSON.stringify({ value: String(v) }) });
        toast(`${d.name} → ${fmt(v)}`);
        dlg.close();
        ack(d.id);
      } catch (e) { toast("Échec : " + e.message); }
    };
    if (slider) {
      slider.oninput = () => {            // en cours de glissement : annule l'envoi
        clearTimeout(sendTimer); sendTimer = null;
        $("#dim-val").textContent = fmt(slider.value);
      };
      slider.onchange = () => {           // relâchement : envoi (différé si tempo)
        clearTimeout(sendTimer);
        if (!delayS) return send(slider.value);
        $("#dim-val").textContent = fmt(slider.value) + " …";
        sendTimer = setTimeout(() => send(slider.value), delayS * 1000);
      };
    }
    body.querySelectorAll("[data-v]").forEach(b =>
      b.onclick = () => { if (slider) slider.value = b.dataset.v;
                          $("#dim-val").textContent = fmt(b.dataset.v);
                          send(b.dataset.v); });
    $("#dim-close").onclick = () => { clearTimeout(sendTimer); dlg.close(); };
    dlg.showModal();
  }

  /* ------------------------------------------------ widget graphe */
  /* Durées des graphes, configurables dans l'admin (« Paramétrage des
     courbes ») et servies par /api/display ; repli sur les mêmes défauts que
     le serveur (db.py:DEFAULT_SETTINGS). mode "raw" = toute la courbe au pas
     de collecte, "minmax" = agrégat min/moy/max horaire ou journalier. */
  const DEFAULT_RANGES = [
    { label: "24 h", span_s: 86400, mode: "raw" },
    { label: "4 j", span_s: 345600, mode: "raw" },
    { label: "15 j", span_s: 1296000, mode: "minmax" },
    { label: "30 j", span_s: 2592000, mode: "minmax" },
    { label: "90 j", span_s: 7776000, mode: "minmax" },
    { label: "6 mois", span_s: 15724800, mode: "minmax" }];
  let chartRanges = DEFAULT_RANGES;
  const rangeMode = span =>
    (chartRanges.find(r => +r.span_s === span) || {}).mode || "auto";

  /* Retourne {box, ready} : ready résout une fois les données de la plage
     initiale chargées — permet à l'appelant (renderPage) d'attendre tous les
     graphes d'une page avant de les afficher, pour éviter un DOM "vide" qui
     s'effondre puis se remplit graphe par graphe. */
  function graphWidget(w, presetSpan) {
    const d = devices[w.device_id] || {};
    const box = document.createElement("div");
    box.className = "chart-box wide";
    if (w.id != null) box.dataset.wid = w.id;
    const head = document.createElement("div");
    head.className = "chart-head";
    head.innerHTML = `<span class="title">${w.options.label || d.name || ""}
      <span class="muted mono" style="font-family:var(--mono)">${d.last_value ?? ""}${d.unit ? " " + d.unit : ""}</span></span>`;
    // Cellule dédiée aux boutons de plage : ils se replient dedans, alignés à
    // droite, face au titre (lui-même sur 1 ou 2 lignes) — au lieu de passer
    // sous le titre et de manger la hauteur du graphe sur mobile.
    const ranges = document.createElement("span");
    ranges.className = "ranges";
    head.appendChild(ranges);
    const plot = document.createElement("div");
    const def = presetSpan || w.options.range_s || 86400;
    const load = async (span, btn) => {
      head.querySelectorAll("button").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      const now = Math.floor(Date.now() / 1000);
      try {
        const data = await api(`/api/series/${d.id}?t_from=${now - span}&t_to=${now}` +
                               `&mode=${rangeMode(span)}`);
        renderChart(plot, data, { unit: d.unit, height: isMobile() ? 200 : 260 });
      } catch (e) { plot.innerHTML = `<p class="muted">${e.message}</p>`; }
    };
    let activeBtn = null;
    for (const r of chartRanges) {
      const b = document.createElement("button");
      b.textContent = r.label;
      b.dataset.span = r.span_s;
      if (+r.span_s === def) activeBtn = b;
      b.onclick = () => load(+r.span_s, b);
      ranges.appendChild(b);
    }
    // Fenêtre mémorisée/du widget absente des durées paramétrées : 1er bouton.
    if (!activeBtn) activeBtn = head.querySelector("button");
    box.appendChild(head); box.appendChild(plot);
    return { box, ready: load(+activeBtn.dataset.span, activeBtn) };
  }

  function openZoom(d) {
    const dlg = $("#zoom-dlg"), body = $("#zoom-body");
    body.innerHTML = "";
    const x = document.createElement("button");
    x.className = "dlg-x"; x.textContent = "✕"; x.title = "Fermer";
    x.onclick = () => dlg.close();
    body.appendChild(x);
    body.appendChild(graphWidget({ device_id: d.id, options: { label: d.name, range_s: 86400 } }).box);
    dlg.showModal();
  }

  /* ------------------------------------------------ journal */
  async function renderJournal() {
    currentPage = null; currentWidgets = [];
    liveTimers.forEach(clearInterval); liveTimers = [];
    document.querySelectorAll("#root-nav a").forEach(a => a.classList.remove("active"));
    $("#page-bg").style.background = "";
    const main = $("#main");
    main.innerHTML = `<h1>Journal de l'application</h1>
      <div class="row" style="max-width:420px">
        <div><label for="j-lvl">Filtrer par niveau</label>
        <select id="j-lvl"><option value="">Tous</option><option>ERROR</option>
        <option>WARNING</option><option>INFO</option><option>DEBUG</option></select></div>
        <div class="fix"><button class="btn" id="j-refresh">Actualiser</button></div>
      </div>
      <p class="muted" id="j-note"></p>
      <table><thead><tr><th>Horodate</th><th>Niveau</th><th>Source</th><th>Message</th></tr></thead>
      <tbody id="j-body"></tbody></table>`;
    const load = async () => {
      const lvl = $("#j-lvl").value;
      const rows = await api(`/api/journal?limit=400${lvl ? "&level=" + lvl : ""}`);
      $("#j-body").innerHTML = rows.map(r =>
        `<tr><td class="mono">${new Date(r.ts * 1000).toLocaleString("fr-FR")}</td>
         <td class="lvl-${r.level}">${r.level}</td><td>${r.source}</td><td>${r.message}</td></tr>`).join("")
        || `<tr><td colspan="4" class="muted">Journal vide.</td></tr>`;
    };
    $("#j-lvl").onchange = load; $("#j-refresh").onclick = load;
    $("#j-note").innerHTML = me.role === "admin"
      ? 'La verbosité du journal (verbeux / moyen / erreurs) se règle dans les ' +
        '<a href="/static/admin.html#general">Réglages généraux</a>.'
      : "La verbosité du journal (verbeux / moyen / erreurs) se règle dans Paramètres.";
    load();
  }

  window.addEventListener("hashchange", () => {
    if (!me) return;
    const h = location.hash.slice(1);
    if (h === "journal") renderJournal();
    else if (h) openPage(parseInt(h));
  });
  boot();
})();
