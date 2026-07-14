/* DomoPi — visualiseur (pages arborescentes, widgets, journal). */
(function () {
  const $ = s => document.querySelector(s);
  let me = null, pages = [], devices = {}, currentPage = null, refreshTimer = null;
  let currentWidgets = [], liveTimers = [];

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
    pages = await api("/api/pages");
    (await api("/api/devices")).forEach(d => devices[d.id] = d);
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
      a.href = "#" + p.id; a.textContent = p.title;
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

    const kids = childPages(id);
    const frag = document.createElement("div");
    frag.innerHTML = (crumbHtml ? `<div class="crumbs">${crumbHtml}</div>` : "") +
                     `<h1>${page.title}</h1>`;
    const grid = document.createElement("div"); grid.className = "grid";
    frag.appendChild(grid);

    for (const k of kids) grid.appendChild(linkCard(k.title, () => openPage(k.id)));
    for (const w of widgets) {
      if (w.wtype === "pagelink" && w.target_page_id)
        grid.appendChild(linkCard(w.options.label || w.target_title || "Page",
                                  () => openPage(w.target_page_id)));
      else if (w.wtype === "label") {
        const c = document.createElement("div");
        c.className = "card wide"; c.style.minHeight = "0";
        c.innerHTML = `<div class="name" style="text-align:left;font-size:.95rem;color:var(--txt)">${w.options.text || ""}</div>`;
        grid.appendChild(c);
      } else if (w.wtype === "graph" && w.device_id)
        grid.appendChild(graphWidget(w));
      else if (w.device_id)
        grid.appendChild(deviceCard(w));
    }
    main.innerHTML = ""; main.appendChild(frag);
    document.querySelectorAll(".chart-head button.active").forEach(b => b.click());
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
    for (const [s, gids] of groups)
      liveTimers.push(setInterval(() => liveTick(gids), s * 1000));
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

  function linkCard(label, onClick) {
    const c = document.createElement("div");
    c.className = "card link clickable"; c.tabIndex = 0;
    c.innerHTML = `<div class="name">📁 ${label}</div>`;
    c.onclick = onClick;
    c.onkeydown = e => { if (e.key === "Enter") onClick(); };
    return c;
  }

  /* ------------------------------------------------ widget périphérique */
  function deviceCard(w) {
    const d = devices[w.device_id] || {};
    const isActuator = d.kind === "actuator";
    const num = parseFloat(String(d.last_value ?? "").replace(",", "."));
    // Pourcentage 0-100 pour les périphériques gradables (lampe à variateur,
    // volet à ouverture partielle...)
    const pct = isActuator && d.dimmable && !isNaN(num)
      ? Math.max(0, Math.min(100, num)) : null;
    const on = pct != null ? pct > 0 : isOn(d.last_value);
    const dim = isActuator && !on;
    const stale = d.last_seen && (Date.now() / 1000 - d.last_seen > 3 * 3600);
    const c = document.createElement("div");
    c.className = "card" + (d.controllable ? " clickable" : "");
    if (w.id) c.dataset.wid = w.id;

    // Icône : pour un état partiel (0 < pct < 100), l'icône "on" est révélée
    // depuis le bas à hauteur du pourcentage, superposée à l'icône "off".
    let iconHtml = "";
    if (isActuator && pct != null && pct > 0 && pct < 100 && d.icon_on && d.icon_off) {
      iconHtml = `<div class="icon"><span class="stack" style="--pct:${pct}%">
        <img src="/static/icons/${d.icon_off}" alt="">
        <img class="top" src="/static/icons/${d.icon_on}" alt=""></span></div>`;
    } else {
      const icon = isActuator
        ? (on ? (d.icon_on || d.icon_off) : (d.icon_off || d.icon_on))
        : (d.icon_on || d.icon_off);
      if (icon) iconHtml = `<div class="icon"><img src="/static/icons/${icon}" alt=""></div>`;
    }
    const valueHtml = pct != null ? `${pct} %`
      : `${d.last_value ?? "—"}${d.unit ? " " + d.unit : ""}`;
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
      if (d.dimmable) {
        // Clic/appui court : marche-arrêt. Double-clic (PC) ou appui long
        // (mobile, avec micro-vibration et zoom du cadre) : réglage 0-100 %.
        c.title = "Clic : marche/arrêt · double-clic ou appui long : réglage 0-100 %";
        const openDetails = () => openDimmer(d, pct ?? (on ? 100 : 0));
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

  /* Curseur 0-100 % pour gradateurs et ouvertures partielles */
  function openDimmer(d, current) {
    const dlg = $("#zoom-dlg"), body = $("#zoom-body");
    body.innerHTML = `<button class="dlg-x" id="dim-close" title="Fermer">✕</button>
      <h2 style="margin-top:0">${d.name}</h2>
      <div class="value" id="dim-val" style="text-align:center;font-size:1.6rem;
           font-family:var(--mono);margin:.2rem 0 .5rem">${current} %</div>
      <input type="range" id="dim-slider" min="0" max="100" step="1"
             value="${current}" style="width:100%;height:2.4rem;touch-action:none">
      <div style="display:flex;justify-content:space-between" class="muted">
        <span>0 %</span><span>100 %</span></div>
      <div class="row" style="margin-top:.7rem">
        ${[0, 25, 50, 75, 100].map(p =>
          `<button class="btn" data-pct="${p}" style="flex:1;min-width:0;padding:.45rem 0">${p}</button>`).join("")}
      </div>
      <p class="muted" style="margin:.6rem 0 0;font-size:.8rem">La consigne part
        automatiquement 1,5&nbsp;s après le relâchement du curseur.</p>`;
    const slider = $("#dim-slider");
    let sendTimer = null;
    const send = async v => {
      clearTimeout(sendTimer); sendTimer = null;
      try {
        await api(`/api/devices/${d.id}/set`, { method: "POST",
          body: JSON.stringify({ value: String(v) }) });
        toast(`${d.name} → ${v} %`);
        dlg.close();
        ack(d.id);
      } catch (e) { toast("Échec : " + e.message); }
    };
    slider.oninput = () => {              // en cours de glissement : annule l'envoi
      clearTimeout(sendTimer); sendTimer = null;
      $("#dim-val").textContent = slider.value + " %";
    };
    slider.onchange = () => {             // relâchement : envoi différé de 1,5 s
      clearTimeout(sendTimer);
      $("#dim-val").textContent = slider.value + " % …";
      sendTimer = setTimeout(() => send(slider.value), 1500);
    };
    body.querySelectorAll("[data-pct]").forEach(b =>
      b.onclick = () => { slider.value = b.dataset.pct;
                          $("#dim-val").textContent = b.dataset.pct + " %";
                          send(b.dataset.pct); });
    $("#dim-close").onclick = () => { clearTimeout(sendTimer); dlg.close(); };
    dlg.showModal();
  }

  /* ------------------------------------------------ widget graphe */
  const RANGES = [["24 h", 86400], ["4 j", 4 * 86400], ["15 j", 15 * 86400],
                  ["30 j", 30 * 86400], ["90 j", 90 * 86400]];

  function graphWidget(w) {
    const d = devices[w.device_id] || {};
    const box = document.createElement("div");
    box.className = "chart-box wide";
    const head = document.createElement("div");
    head.className = "chart-head";
    head.innerHTML = `<span class="title">${w.options.label || d.name || ""}
      <span class="muted mono" style="font-family:var(--mono)">${d.last_value ?? ""}${d.unit ? " " + d.unit : ""}</span></span>`;
    const plot = document.createElement("div");
    const def = w.options.range_s || 86400;
    for (const [lbl, span] of RANGES) {
      const b = document.createElement("button");
      b.textContent = lbl;
      if (span === def) b.className = "active";
      b.onclick = async () => {
        head.querySelectorAll("button").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        const now = Math.floor(Date.now() / 1000);
        try {
          const data = await api(`/api/series/${d.id}?t_from=${now - span}&t_to=${now}`);
          renderChart(plot, data, { unit: d.unit, height: isMobile() ? 200 : 260 });
        } catch (e) { plot.innerHTML = `<p class="muted">${e.message}</p>`; }
      };
      head.appendChild(b);
    }
    box.appendChild(head); box.appendChild(plot);
    queueMicrotask(() => head.querySelector("button.active")?.click());
    return box;
  }

  function openZoom(d) {
    const dlg = $("#zoom-dlg"), body = $("#zoom-body");
    body.innerHTML = "";
    const x = document.createElement("button");
    x.className = "dlg-x"; x.textContent = "✕"; x.title = "Fermer";
    x.onclick = () => dlg.close();
    body.appendChild(x);
    body.appendChild(graphWidget({ device_id: d.id, options: { label: d.name, range_s: 86400 } }));
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
