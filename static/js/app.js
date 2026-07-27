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

  /* Tuiles, dialogue de réglage et graphes sont partagés avec l'admin
     (tile.js) : on lui passe ici le contexte du visualiseur. */
  DomoTile.configure({
    api, toast, mobile: isMobile,
    dlg: $("#zoom-dlg"), body: $("#zoom-body"),
    onAck: id => ackRefresh(id),
  });
  const deviceCard = w => DomoTile.card(devices[w.device_id] || {},
    { label: w.options.label, wid: w.id });

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
    DomoTile.setDisplay({ sig: Math.max(3, parseInt(dp.display_sig_digits, 10) || 5),
                          thou: dp.display_thousands_sep ?? " ",
                          dec: dp.display_decimal_sep || "," });
    try {
      DomoTile.setRanges(JSON.parse(dp.chart_ranges));
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
    // mémorise ici le choix de plage (1J/4J/15J) et le zoom de chaque graphe,
    // ainsi que le défilement de la page, pour les restaurer après coup —
    // sinon l'utilisateur les perd à chaque rafraîchissement périodique.
    const prevRanges = new Map(), prevZooms = new Map();
    let prevScrollY = null;
    if (soft) {
      main.querySelectorAll(".chart-box[data-wid]").forEach(box => {
        const btn = box.querySelector(".chart-head button.active");
        if (btn) prevRanges.set(box.dataset.wid, +btn.dataset.span);
        if (box._zoom) prevZooms.set(box.dataset.wid, box._zoom);
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
        const gw = graphWidget(w, prevRanges.get(String(w.id)),
                               prevZooms.get(String(w.id)));
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

  /* ------------------------------------------------ widgets périphérique / graphe */
  /* Tuile, dialogue de réglage sur échelle, graphe et ses boutons de plage
     vivent dans tile.js (partagés avec l'admin) ; `deviceCard` est défini
     plus haut, à la configuration du module. */
  const graphWidget = (w, presetSpan, presetZoom) =>
    DomoTile.chart(devices[w.device_id] || {}, {
      label: w.options.label, wid: w.id,
      span: presetSpan || w.options.range_s || 86400, zoom: presetZoom });

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
