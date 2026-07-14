/* DomoPi — interface d'administration. */
(function () {
  const $ = s => document.querySelector(s);
  let icons = [], backgrounds = [], devices = [], pages = [], currentWePage = null;

  const api = async (url, opts) => {
    const r = await fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    if (r.status === 401 || r.status === 403) { location.href = "/"; throw new Error("auth"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  };
  const toast = msg => {
    const t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
  };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function dialog(html, onOpen) {
    const dlg = $("#dlg"); $("#dlg-body").innerHTML = html;
    if (onOpen) onOpen(dlg);
    dlg.showModal();
    return dlg;
  }

  /* ============================================ réglages */
  async function loadSettings() {
    const s = await api("/api/settings");
    $("#s-title").value = s.site_title || "";
    $("#s-interval").value = s.poll_interval_s || 300;
    $("#s-retention").value = s.raw_retention_days || 120;
    $("#s-jlevel").value = s.journal_level || "medium";
    $("#s-jret").value = s.journal_retention_days || 30;
  }
  $("#s-save").onclick = async () => {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({
      site_title: $("#s-title").value,
      poll_interval_s: $("#s-interval").value,
      raw_retention_days: $("#s-retention").value,
      journal_level: $("#s-jlevel").value,
      journal_retention_days: $("#s-jret").value }) });
    toast("Réglages enregistrés");
  };

  /* ============================================ connecteurs */
  const CONN_FIELDS = {
    eedomus: [["host", "Adresse IP de la box eedomus"],
              ["api_user", "api_user (portail eedomus → Mon compte → Paramètres → API)"],
              ["api_secret", "api_secret"]],
    wes_mqtt: [["host", "Broker MQTT (127.0.0.1 = Mosquitto local du Raspberry)"],
               ["port", "Port (1883)"],
               ["username", "Utilisateur MQTT (optionnel)"],
               ["password", "Mot de passe MQTT (optionnel)"],
               ["discovery_prefix", "Préfixe discovery (homeassistant)"]],
  };
  const CONN_DEFAULTS = {
    eedomus: { host: "", api_user: "", api_secret: "" },
    wes_mqtt: { host: "127.0.0.1", port: 1883, username: "", password: "",
                discovery_prefix: "homeassistant" },
  };

  async function loadConnectors() {
    const list = await api("/api/connectors");
    $("#conn-list").innerHTML = list.length ? "" :
      "<p class='muted'>Aucun contrôleur configuré pour l'instant.</p>";
    for (const c of list) {
      const div = document.createElement("div");
      div.className = "card"; div.style.marginBottom = ".5rem"; div.style.minHeight = "0";
      div.innerHTML = `<div class="row">
        <div><b>${esc(c.name)}</b> <span class="muted">(${c.type}${c.enabled ? "" : " — désactivé"})</span></div>
        <div class="fix"><button class="btn sm" data-a="edit">Configurer</button></div>
        <div class="fix"><button class="btn sm" data-a="disc">Découvrir les périphériques</button></div>
        <div class="fix"><button class="btn sm danger" data-a="del">Supprimer</button></div></div>`;
      div.querySelector('[data-a=edit]').onclick = () => connectorForm(c);
      div.querySelector('[data-a=del]').onclick = async () => {
        if (!confirm(`Supprimer « ${c.name} » et tous ses périphériques/historiques ?`)) return;
        await api(`/api/connectors/${c.id}`, { method: "DELETE" });
        loadConnectors(); loadDevices();
      };
      div.querySelector('[data-a=disc]').onclick = async ev => {
        ev.target.disabled = true; ev.target.textContent = "Découverte…";
        try {
          const r = await api(`/api/connectors/${c.id}/discover`);
          toast(`${r.count} périphériques découverts`);
          if (!r.count && c.type === "wes_mqtt") {
            // Deux pannes distinctes : DomoPi <-> broker local, ou WES <-> broker.
            if (r.broker_connected === false) brokerHelp(c);
            else wesMqttHelp(c, null, r.server_ip);
          }
          loadDevices();
        } catch (e) {
          toast("Échec : " + e.message);
          if (c.type === "wes_mqtt") brokerHelp(c, e.message);
        }
        ev.target.disabled = false; ev.target.textContent = "Découvrir les périphériques";
      };
      $("#conn-list").appendChild(div);
    }
  }

  function connectorForm(c) {
    const fields = CONN_FIELDS[c.type].map(([k, lbl]) =>
      `<label>${lbl}</label><input data-k="${k}" type="text" value="${esc(c.config[k] ?? "")}">`).join("");
    dialog(`<h2 style="margin-top:0">${c.id ? "Configurer" : "Ajouter"} — ${c.type}</h2>
      <label>Nom</label><input id="cf-name" type="text" value="${esc(c.name)}">
      ${fields}
      <label><input type="checkbox" id="cf-en" ${c.enabled ? "checked" : ""}
        style="width:auto"> Connecteur actif</label>
      <div class="row" style="margin-top:1rem">
        <button class="btn primary" id="cf-save">Enregistrer</button>
        <button class="btn" id="cf-cancel">Annuler</button></div>`, dlg => {
      dlg.querySelector("#cf-cancel").onclick = () => dlg.close();
      dlg.querySelector("#cf-save").onclick = async () => {
        const config = {};
        dlg.querySelectorAll("[data-k]").forEach(i => config[i.dataset.k] = i.value);
        const body = { type: c.type, name: dlg.querySelector("#cf-name").value,
                       enabled: dlg.querySelector("#cf-en").checked, config };
        if (c.id) await api(`/api/connectors/${c.id}`, { method: "PUT", body: JSON.stringify(body) });
        else await api("/api/connectors", { method: "POST", body: JSON.stringify(body) });
        dlg.close(); toast("Connecteur enregistré"); loadConnectors();
      };
    });
  }
  /* Aide affichée quand DomoPi lui-même n'est pas connecté au broker MQTT
     (le problème est en amont du WES : sa config n'y changerait rien). */
  function brokerHelp(c, errMsg) {
    const cfg = c.config || {};
    dialog(`<h2 style="margin-top:0">DomoPi n'est pas connecté au broker MQTT</h2>
      ${errMsg ? `<p class="muted">Erreur : ${esc(errMsg)}</p>` : ""}
      <p>La découverte ne peut rien recevoir : c'est la connexion entre
         <b>DomoPi et le broker Mosquitto</b> qui échoue — inutile de toucher
         au WES pour l'instant. Vérifiez, via « Configurer » :</p>
      <ul style="line-height:1.6">
        <li><b>Broker MQTT</b> : <span class="mono">${esc(cfg.host || "127.0.0.1")}</span>
            (Mosquitto local du Raspberry = <span class="mono">127.0.0.1</span>),
            port <span class="mono">${esc(cfg.port || 1883)}</span> ;</li>
        <li><b>Utilisateur / mot de passe MQTT</b> : <u>obligatoires</u> — le
            broker refuse les connexions anonymes. Ce sont les identifiants
            MQTT choisis à l'installation de DomoPi
            ${cfg.username ? "" : "(le champ utilisateur est actuellement vide)"} ;</li>
        <li>le service sur le Pi : <span class="mono">systemctl status mosquitto</span> ;</li>
        <li>le <b>Journal</b> (bandeau) : « connexion refusée » = identifiants
            incorrects, « injoignable » = hôte/port ou service arrêté.</li>
      </ul>
      <p>Après correction, ré-enregistrez le connecteur (il se reconnecte
         aussitôt), attendez quelques secondes puis relancez la découverte.
         Si vous venez tout juste d'ajouter le connecteur, la connexion peut
         simplement ne pas être encore établie : réessayez.</p>
      <div class="row" style="margin-top:.8rem">
        <div class="fix"><button class="btn" id="bh-close">Fermer</button></div>
      </div>`, dlg => {
      dlg.querySelector("#bh-close").onclick = () => dlg.close();
    });
  }

  /* Aide affichée quand la découverte WES ne trouve rien alors que DomoPi
     est bien connecté au broker : reprend la présentation de la page
     « Configuration MQTT » du WES (MQTTCFG.HTM) avec les valeurs attendues,
     préremplies depuis le connecteur. serverIp = IPv4 LAN du Pi (API). */
  function wesMqttHelp(c, errMsg, serverIp) {
    const cfg = c.config || {};
    const isIp = h => /^\d{1,3}(\.\d{1,3}){3}$/.test(h);
    let broker = cfg.host;
    if (!broker || broker === "127.0.0.1" || broker === "localhost")
      broker = serverIp || (isIp(location.hostname) ? location.hostname
                                                    : "l'adresse IPv4 du Raspberry");
    const px = cfg.discovery_prefix || "homeassistant";
    dialog(`<h2 style="margin-top:0">Aucun périphérique WES découvert</h2>
      ${errMsg ? `<p class="muted">Erreur : ${esc(errMsg)}</p>` : ""}
      <p>Vérifiez la page <b>Configuration MQTT</b> du WES
         (<span class="mono">http://&lt;ip-du-wes&gt;/MQTTCFG.HTM</span>) —
         les valeurs attendues :</p>
      <div class="wes-help">
        <div class="wes-panel">
          <div class="wes-head">⇄ Configuration des paramètres MQTT</div>
          <table>
            <tr><th>État MQTT</th><td><span class="wes-on">ON</span></td></tr>
            <tr><th>Adresse IP ou nom du broker</th>
              <td><b class="mono">${esc(broker)}</b>
              <small>Indiquez une adresse IP : le WES ne sait pas résoudre les
              noms Windows du réseau local (type PI-SERVER).</small></td></tr>
            <tr><th>Port du broker</th><td class="mono">${esc(cfg.port || 1883)}</td></tr>
            <tr><th>Username</th><td>${cfg.username
              ? `<b class="mono">${esc(cfg.username)}</b>`
              : `<b>requis</b> — le broker refuse les connexions anonymes ;
                 utilisez les identifiants MQTT choisis à l'installation
                 (et renseignez-les aussi dans le connecteur DomoPi)`}</td></tr>
            <tr><th>Password</th><td>le mot de passe MQTT associé
              (identifiants définis à l'installation du broker)</td></tr>
            <tr><th>Keep-alive (secondes)</th><td class="mono">60</td></tr>
            <tr><th>QoS par défaut</th><td>0 - Au plus une fois</td></tr>
            <tr class="crucial"><th>MQTT Discovery</th>
              <td><span class="wes-on">ON</span>
              <small>Indispensable : c'est lui qui publie les périphériques
              que DomoPi découvre.</small></td></tr>
            <tr><th>Préfixe Discovery</th><td>${px === "homeassistant"
              ? "Home Assistant (homeassistant)"
              : `Personnalisé… → <span class="mono">${esc(px)}</span>`}</td></tr>
            <tr><th>Activer les Topics à envoyer</th><td>cochez les familles de
              valeurs voulues (TIC, pinces, sondes, relais, impulsions…)</td></tr>
            <tr><th>Lancer le Discovery manuellement</th><td>après « Enregistrer »,
              cliquez <b>Envoyer Discovery</b></td></tr>
          </table>
        </div>
        <p><b>État de la connexion</b> (le rond sur la page du WES) :
          <span class="dot" style="background:#5cb85c"></span> vert = connecté ·
          <span class="dot" style="background:#428bca"></span> bleu = MQTT actif
          mais connexion inactive ·
          <span class="dot" style="background:#f0ad4e"></span> orange = connexion
          en cours ou perdue ·
          <span class="dot" style="background:#c0020b"></span> rouge = erreur.
          S'il reste bleu ou orange, revoyez l'adresse du broker et les
          identifiants.</p>
        <p>Une fois le voyant vert, patientez ~30&nbsp;s (le temps que le WES
           publie ses entités), puis relancez « Découvrir les périphériques ».</p>
      </div>
      <div class="row" style="margin-top:.8rem">
        <div class="fix"><button class="btn" id="wes-close">Fermer</button></div>
      </div>`, dlg => {
      dlg.querySelector("#wes-close").onclick = () => dlg.close();
    });
  }

  $("#conn-add-eedomus").onclick = () =>
    connectorForm({ type: "eedomus", name: "eedomus", enabled: true, config: { ...CONN_DEFAULTS.eedomus } });
  $("#conn-add-wes").onclick = () =>
    connectorForm({ type: "wes_mqtt", name: "WES", enabled: true, config: { ...CONN_DEFAULTS.wes_mqtt } });

  /* ============================================ périphériques */
  async function loadDevices() {
    devices = await api("/api/devices");
    renderDevices();
  }
  let devSort = { key: "", dir: 1 };

  function renderDevices() {
    const f = ($("#dev-filter").value || "").toLowerCase();
    let rows = devices.filter(d =>
      !f || [d.name, d.room, d.connector_name].join(" ").toLowerCase().includes(f));
    document.querySelectorAll("#dev-filters [data-f]").forEach(el => {
      const v = el.value, k = el.dataset.f;
      if (v === "") return;
      rows = el.tagName === "SELECT"
        ? rows.filter(d => (k === "kind" ? d.kind : (d[k] ? "1" : "0")) === v)
        : rows.filter(d => String(d[k] ?? "").toLowerCase().includes(v.toLowerCase()));
    });
    if (devSort.key) {
      const { key, dir } = devSort;
      rows.sort((a, b) => {
        const va = a[key], vb = b[key];
        if (typeof va === "number" || typeof vb === "number") return ((va || 0) - (vb || 0)) * dir;
        return String(va ?? "").localeCompare(String(vb ?? ""), "fr", { sensitivity: "base" }) * dir;
      });
    }
    $("#dev-body").innerHTML = rows.map(d => `<tr data-id="${d.id}">
      <td><input type="checkbox" data-k="monitored" ${d.monitored ? "checked" : ""}></td>
      <td><input type="text" data-k="name" value="${esc(d.name)}" style="min-width:130px"></td>
      <td>${esc(d.room)}</td><td>${esc(d.connector_name)}</td>
      <td>${d.kind === "actuator" ? "sortie" : "capteur"}</td>
      <td><input type="text" data-k="unit" value="${esc(d.unit)}" style="width:56px"></td>
      <td>${d.kind === "actuator"
        ? `<input type="checkbox" data-k="controllable" ${d.controllable ? "checked" : ""}>` : "—"}</td>
      <td>${d.kind === "actuator"
        ? `<input type="checkbox" data-k="dimmable" ${d.dimmable ? "checked" : ""}
             title="Pilotage en pourcentage (gradateur, ouverture partielle)">` : "—"}</td>
      <td><img data-pick="icon_on" src="${d.icon_on ? "/static/icons/" + d.icon_on : ""}"
           alt="on" title="icône état actif" style="width:26px;height:26px;cursor:pointer;background:var(--panel-2);border-radius:5px;padding:2px">
          <img data-pick="icon_off" src="${d.icon_off ? "/static/icons/" + d.icon_off : ""}"
           alt="off" title="icône état inactif" style="width:26px;height:26px;cursor:pointer;background:var(--panel-2);border-radius:5px;padding:2px"></td>
      </tr>`).join("") || `<tr><td colspan="9" class="muted">Aucun périphérique.
        Utilisez « Découvrir les périphériques » sur un contrôleur.</td></tr>`;

    $("#dev-body").querySelectorAll("tr[data-id]").forEach(tr => {
      const d = devices.find(x => x.id === +tr.dataset.id);
      const save = async patch => {
        Object.assign(d, patch);
        await api(`/api/devices/${d.id}`, { method: "PUT", body: JSON.stringify(d) });
      };
      tr.querySelectorAll("input[data-k]").forEach(inp => inp.onchange = () =>
        save({ [inp.dataset.k]: inp.type === "checkbox" ? inp.checked : inp.value })
          .then(() => toast("Enregistré")).catch(e => toast(e.message)));
      tr.querySelectorAll("img[data-pick]").forEach(img => img.onclick = () =>
        pickIcon(chosen => save({ [img.dataset.pick]: chosen }).then(() => {
          img.src = chosen ? "/static/icons/" + chosen : ""; toast("Icône mise à jour");
        })));
    });
  }
  $("#dev-filter").oninput = renderDevices;
  document.querySelectorAll("#dev-filters [data-f]").forEach(el => el.oninput = renderDevices);
  document.querySelectorAll("#dev-head th[data-sort]").forEach(th => th.onclick = () => {
    const k = th.dataset.sort;
    devSort = devSort.key === k ? { key: k, dir: -devSort.dir } : { key: k, dir: 1 };
    document.querySelectorAll("#dev-head .arrow").forEach(a => a.remove());
    th.insertAdjacentHTML("beforeend",
      `<span class="arrow">${devSort.dir > 0 ? " ▲" : " ▼"}</span>`);
    renderDevices();
  });

  function pickIcon(cb) {
    dialog(`<h2 style="margin-top:0">Choisir une icône</h2>
      <div class="icon-pick" id="pick">${icons.map(i =>
        `<img src="/static/icons/${i}" title="${i}" data-i="${i}">`).join("")}</div>
      <div class="row" style="margin-top:.8rem">
        <button class="btn" id="pick-none">Aucune icône</button>
        <button class="btn" id="pick-cancel">Annuler</button></div>`, dlg => {
      dlg.querySelectorAll("#pick img").forEach(img =>
        img.onclick = () => { cb(img.dataset.i); dlg.close(); });
      dlg.querySelector("#pick-none").onclick = () => { cb(""); dlg.close(); };
      dlg.querySelector("#pick-cancel").onclick = () => dlg.close();
    });
  }

  /* ============================================ pages */
  async function loadPages() {
    pages = await api("/api/pages");
    const tree = $("#pages-tree"); tree.innerHTML = "";
    const render = (parent, depth) => {
      for (const p of pages.filter(x => x.parent_id === parent)
                           .sort((a, b) => a.sort_order - b.sort_order)) {
        const div = document.createElement("div");
        div.className = "card"; div.style.minHeight = "0";
        div.style.marginLeft = (depth * 1.4) + "rem"; div.style.marginBottom = ".4rem";
        div.innerHTML = `<div class="row">
          <div><b>${esc(p.title)}</b>
            <span class="muted">${p.dual_layout ? " · double rendu" : ""}${p.background ? " · fond" : ""}</span></div>
          <div class="fix"><button class="btn sm" data-a="w">Widgets</button></div>
          <div class="fix"><button class="btn sm" data-a="e">Modifier</button></div>
          <div class="fix"><button class="btn sm" data-a="c">+ Sous-page</button></div>
          <div class="fix"><button class="btn sm danger" data-a="d">Suppr.</button></div></div>`;
        div.querySelector("[data-a=e]").onclick = () => pageForm(p);
        div.querySelector("[data-a=c]").onclick = () =>
          pageForm({ parent_id: p.id, title: "", background: "", dual_layout: 0, sort_order: 0 });
        div.querySelector("[data-a=w]").onclick = () => openWidgetsEditor(p);
        div.querySelector("[data-a=d]").onclick = async () => {
          if (!confirm(`Supprimer la page « ${p.title} » et ses sous-pages ?`)) return;
          await api(`/api/pages/${p.id}`, { method: "DELETE" }); loadPages();
        };
        tree.appendChild(div);
        render(p.id, depth + 1);
      }
    };
    render(null, 0);
    if (!pages.length) tree.innerHTML = "<p class='muted'>Aucune page pour l'instant.</p>";
  }
  $("#page-add-root").onclick = () =>
    pageForm({ parent_id: null, title: "", background: "", dual_layout: 0, sort_order: 0 });

  function pageForm(p) {
    const parentOpts = [`<option value="">— racine —</option>`].concat(
      pages.filter(x => x.id !== p.id).map(x =>
        `<option value="${x.id}" ${x.id === p.parent_id ? "selected" : ""}>${esc(x.title)}</option>`)).join("");
    const bgOpts = [`<option value="">— aucun / couleur ci-dessous —</option>`].concat(
      backgrounds.map(b => `<option ${p.background === b ? "selected" : ""}>${b}</option>`)).join("");
    dialog(`<h2 style="margin-top:0">${p.id ? "Modifier la page" : "Nouvelle page"}</h2>
      <label>Titre</label><input id="pf-title" type="text" value="${esc(p.title)}">
      <label>Page parente</label><select id="pf-parent">${parentOpts}</select>
      <label>Fond : image</label><select id="pf-bgimg">${bgOpts}</select>
      <label>Fond : couleur CSS (ex. #1a2430) — utilisé si aucune image</label>
      <input id="pf-bgcol" type="text" value="${p.background && p.background.startsWith("#") ? esc(p.background) : ""}">
      <label>Ordre d'affichage</label><input id="pf-order" type="number" value="${p.sort_order || 0}">
      <label><input type="checkbox" id="pf-dual" ${p.dual_layout ? "checked" : ""} style="width:auto">
        Double rendu smartphone / PC (chaque widget peut cibler un des deux rendus)</label>
      <div class="row" style="margin-top:1rem">
        <button class="btn primary" id="pf-save">Enregistrer</button>
        <button class="btn" id="pf-cancel">Annuler</button></div>`, dlg => {
      dlg.querySelector("#pf-cancel").onclick = () => dlg.close();
      dlg.querySelector("#pf-save").onclick = async () => {
        const body = {
          title: dlg.querySelector("#pf-title").value || "Sans titre",
          parent_id: dlg.querySelector("#pf-parent").value ? +dlg.querySelector("#pf-parent").value : null,
          background: dlg.querySelector("#pf-bgimg").value || dlg.querySelector("#pf-bgcol").value,
          dual_layout: dlg.querySelector("#pf-dual").checked,
          sort_order: +dlg.querySelector("#pf-order").value || 0 };
        if (p.id) await api(`/api/pages/${p.id}`, { method: "PUT", body: JSON.stringify(body) });
        else await api("/api/pages", { method: "POST", body: JSON.stringify(body) });
        dlg.close(); loadPages();
      };
    });
  }

  /* ============================================ widgets */
  async function openWidgetsEditor(page) {
    currentWePage = page;
    $("#widgets-editor").hidden = false;
    $("#we-title").textContent = `Widgets de la page « ${page.title} »`;
    const ws = await api(`/api/pages/${page.id}/widgets`);
    $("#we-body").innerHTML = ws.map(w => `<tr>
      <td>${w.sort_order}</td>
      <td>${{ device: "périphérique", graph: "graphe", pagelink: "lien de page", label: "texte" }[w.wtype] || w.wtype}</td>
      <td>${esc(w.device_name || w.target_title || w.options.text || "")}</td>
      <td>${{ both: "les deux", mobile: "smartphone", desktop: "PC" }[w.layout]}</td>
      <td>${esc(w.options.label || "")}</td>
      <td><button class="btn sm" data-e="${w.id}">Modifier</button>
          <button class="btn sm danger" data-d="${w.id}">Suppr.</button></td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">Aucun widget sur cette page.</td></tr>`;
    $("#we-body").querySelectorAll("[data-d]").forEach(b => b.onclick = async () => {
      await api(`/api/widgets/${b.dataset.d}`, { method: "DELETE" });
      openWidgetsEditor(page);
    });
    $("#we-body").querySelectorAll("[data-e]").forEach(b => b.onclick = () =>
      widgetForm(ws.find(w => w.id === +b.dataset.e)));
    $("#we-add").onclick = () => widgetForm({ page_id: page.id, wtype: "device",
      layout: "both", sort_order: 0, options: {} });
    $("#widgets-editor").scrollIntoView({ behavior: "smooth" });
  }

  function widgetForm(w) {
    const devOpts = devices.map(d =>
      `<option value="${d.id}" ${d.id === w.device_id ? "selected" : ""}>${esc(d.name)} (${esc(d.room || d.connector_name)})</option>`).join("");
    const pageOpts = pages.map(p =>
      `<option value="${p.id}" ${p.id === w.target_page_id ? "selected" : ""}>${esc(p.title)}</option>`).join("");
    dialog(`<h2 style="margin-top:0">${w.id ? "Modifier le widget" : "Nouveau widget"}</h2>
      <label>Type</label><select id="wf-type">
        <option value="device" ${w.wtype === "device" ? "selected" : ""}>Périphérique (icône + valeur)</option>
        <option value="graph" ${w.wtype === "graph" ? "selected" : ""}>Graphe (courbes historiques)</option>
        <option value="pagelink" ${w.wtype === "pagelink" ? "selected" : ""}>Lien vers une page</option>
        <option value="label" ${w.wtype === "label" ? "selected" : ""}>Texte libre</option></select>
      <div id="wf-dev"><label>Périphérique</label><select id="wf-device">${devOpts}</select></div>
      <div id="wf-page"><label>Page cible</label><select id="wf-target">${pageOpts}</select></div>
      <div id="wf-text"><label>Texte</label><input id="wf-textv" type="text" value="${esc(w.options.text || "")}"></div>
      <label>Libellé affiché (optionnel)</label>
      <input id="wf-label" type="text" value="${esc(w.options.label || "")}">
      <div class="row">
        <div><label>Rendu</label><select id="wf-layout">
          <option value="both" ${w.layout === "both" ? "selected" : ""}>Smartphone et PC</option>
          <option value="mobile" ${w.layout === "mobile" ? "selected" : ""}>Smartphone uniquement</option>
          <option value="desktop" ${w.layout === "desktop" ? "selected" : ""}>PC uniquement</option></select></div>
        <div><label>Ordre</label><input id="wf-order" type="number" value="${w.sort_order || 0}"></div>
        <div id="wf-range"><label>Fenêtre par défaut du graphe</label><select id="wf-rangev">
          ${[["86400", "24 h"], ["345600", "4 jours"], ["1296000", "15 jours"], ["2592000", "30 jours"]]
            .map(([v, l]) => `<option value="${v}" ${String(w.options.range_s || 86400) === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></div>
      </div>
      <div class="row" style="margin-top:1rem">
        <button class="btn primary" id="wf-save">Enregistrer</button>
        <button class="btn" id="wf-cancel">Annuler</button></div>`, dlg => {
      const sync = () => {
        const t = dlg.querySelector("#wf-type").value;
        dlg.querySelector("#wf-dev").hidden = !(t === "device" || t === "graph");
        dlg.querySelector("#wf-page").hidden = t !== "pagelink";
        dlg.querySelector("#wf-text").hidden = t !== "label";
        dlg.querySelector("#wf-range").hidden = t !== "graph";
      };
      dlg.querySelector("#wf-type").onchange = sync; sync();
      dlg.querySelector("#wf-cancel").onclick = () => dlg.close();
      dlg.querySelector("#wf-save").onclick = async () => {
        const t = dlg.querySelector("#wf-type").value;
        const body = { wtype: t, layout: dlg.querySelector("#wf-layout").value,
          sort_order: +dlg.querySelector("#wf-order").value || 0,
          device_id: (t === "device" || t === "graph") ? +dlg.querySelector("#wf-device").value : null,
          target_page_id: t === "pagelink" ? +dlg.querySelector("#wf-target").value : null,
          options: { label: dlg.querySelector("#wf-label").value,
                     text: dlg.querySelector("#wf-textv").value,
                     range_s: +dlg.querySelector("#wf-rangev").value } };
        if (w.id) await api(`/api/widgets/${w.id}`, { method: "PUT", body: JSON.stringify(body) });
        else await api(`/api/pages/${currentWePage.id}/widgets`, { method: "POST", body: JSON.stringify(body) });
        dlg.close(); openWidgetsEditor(currentWePage);
      };
    });
  }

  /* ============================================ utilisateurs */
  async function loadUsers() {
    const users = await api("/api/users");
    $("#users-body").innerHTML = users.map(u => `<tr>
      <td>${esc(u.username)}</td><td>${u.role === "admin" ? "Admin" : "Lecteur"}</td>
      <td><button class="btn sm" data-p="${u.id}">Mot de passe</button>
          <button class="btn sm danger" data-d="${u.id}">Suppr.</button></td></tr>`).join("");
    $("#users-body").querySelectorAll("[data-d]").forEach(b => b.onclick = async () => {
      if (!confirm("Supprimer cet utilisateur ?")) return;
      try { await api(`/api/users/${b.dataset.d}`, { method: "DELETE" }); loadUsers(); }
      catch (e) { toast(e.message); }
    });
    $("#users-body").querySelectorAll("[data-p]").forEach(b => b.onclick = async () => {
      const p = prompt("Nouveau mot de passe :");
      if (!p) return;
      await api(`/api/users/${b.dataset.p}/password`, { method: "PUT",
        body: JSON.stringify({ password: p }) });
      toast("Mot de passe modifié");
    });
  }
  $("#u-add").onclick = async () => {
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify({
        username: $("#u-name").value, password: $("#u-pass").value,
        role: $("#u-role").value }) });
      $("#u-name").value = ""; $("#u-pass").value = ""; loadUsers();
    } catch (e) { toast(e.message); }
  };

  /* ============================================ uploads */
  async function loadGalleries() {
    icons = await api("/api/icons");
    backgrounds = await api("/api/backgrounds");
    $("#icons-gallery").innerHTML = icons.map(i =>
      `<img src="/static/icons/${i}" title="${i}">`).join("");
  }
  async function upload(inputSel, endpoint) {
    const file = $(inputSel).files[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(endpoint, { method: "POST", body: fd });
    if (!r.ok) return toast((await r.json().catch(() => ({}))).detail || "Échec de l'envoi");
    toast("Fichier ajouté"); $(inputSel).value = ""; loadGalleries();
  }
  $("#up-icon").onchange = () => upload("#up-icon", "/api/icons/upload");
  $("#up-bg").onchange = () => upload("#up-bg", "/api/backgrounds/upload");

  /* ============================================ init */
  $("#btn-logout").onclick = async () => {
    await api("/api/logout", { method: "POST" }); location.href = "/";
  };
  (async () => {
    const meR = await fetch("/api/me");
    if (!meR.ok) return location.href = "/";
    const me = await meR.json();
    if (me.role !== "admin") return location.href = "/";
    $("#who").textContent = me.username + " (admin)";
    await Promise.all([loadSettings(), loadGalleries()]);
    await Promise.all([loadConnectors(), loadDevices(), loadPages(), loadUsers()]);
  })();
})();
