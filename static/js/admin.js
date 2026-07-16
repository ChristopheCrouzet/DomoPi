/* DomoPi — interface d'administration. */
(function () {
  const $ = s => document.querySelector(s);
  let icons = [], backgrounds = [], devices = [], pages = [], scales = [],
      currentWePage = null;

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
  const LIVE_FIELD = ["live_refresh_s",
    "Rafraîchissement des widgets affichés (secondes, 0 = désactivé, min 5)"];
  const CONN_FIELDS = {
    eedomus: [["host", "Adresse IP de la box eedomus"],
              ["api_user", "api_user (portail eedomus → Mon compte → Paramètres → API)"],
              ["api_secret", "api_secret"],
              LIVE_FIELD],
    wes_mqtt: [["host", "Broker MQTT (127.0.0.1 = Mosquitto local du Raspberry)"],
               ["port", "Port (1883)"],
               ["username", "Utilisateur MQTT (optionnel)"],
               ["password", "Mot de passe MQTT (optionnel)"],
               ["discovery_prefix", "Préfixe discovery (homeassistant)"],
               LIVE_FIELD],
    yamaha: [["host", "Adresse IP de l'ampli Yamaha"],
             ["vol_min_db", "Volume à 0 % (dB, défaut -80.5)"],
             ["vol_max_db", "Volume à 100 % (dB, défaut 16.5 — mettre 0 pour un maxi raisonnable)"],
             LIVE_FIELD],
  };
  const CONN_DEFAULTS = {
    eedomus: { host: "", api_user: "", api_secret: "", live_refresh_s: 10 },
    wes_mqtt: { host: "127.0.0.1", port: 1883, username: "", password: "",
                discovery_prefix: "homeassistant", live_refresh_s: 10 },
    yamaha: { host: "", vol_min_db: -80.5, vol_max_db: 16.5, live_refresh_s: 10 },
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
            // Trois cas : DomoPi <-> broker en échec, connexion toute récente
            // (annonces pas encore reçues), ou vrai problème côté WES.
            if (r.broker_connected === false) brokerHelp(c);
            else if ((r.connected_s ?? 999) < 30) waitHelp(r.connected_s);
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
        <li>le <a href="/#journal" target="_blank"><b>Journal</b></a> :
            « connexion refusée » = identifiants incorrects,
            « injoignable » = hôte/port ou service arrêté.</li>
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

  /* Cas « il faut juste attendre » : DomoPi vient de se (re)connecter au
     broker (après correction de la config ou redémarrage), les annonces du
     WES ne sont probablement pas encore arrivées. */
  function waitHelp(connectedS) {
    dialog(`<h2 style="margin-top:0">Connexion au broker toute récente</h2>
      <p>DomoPi s'est connecté au broker MQTT il y a
         <b>${esc(connectedS ?? "quelques")}&nbsp;s</b> : les annonces du WES
         n'ont probablement pas encore été reçues — ce n'est sans doute
         <b>pas un problème de configuration</b>.</p>
      <p>Patientez une trentaine de secondes puis relancez
         « Découvrir les périphériques ». Si rien ne vient au bout de
         quelques essais, ouvrez la page MQTT du WES et cliquez
         « Envoyer Discovery », ou revoyez sa configuration.</p>
      <div class="row" style="margin-top:.8rem">
        <div class="fix"><button class="btn" id="wh-close">Fermer</button></div>
      </div>`, dlg => {
      dlg.querySelector("#wh-close").onclick = () => dlg.close();
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
  $("#conn-add-yamaha").onclick = () =>
    connectorForm({ type: "yamaha", name: "Ampli Yamaha", enabled: true, config: { ...CONN_DEFAULTS.yamaha } });

  /* ============================================ périphériques */
  async function loadDevices() {
    devices = await api("/api/devices");
    // Nom d'échelle à plat pour le tri et l'affichage de la colonne « Échelle ».
    devices.forEach(d =>
      d.scale_name = (scales.find(s => s.id === d.scale_id) || {}).name || "");
    renderDevices();
    renderScales();      // compteurs « n périphériques » des cartes d'échelles
  }
  let devSort = { key: "", dir: 1 };

  /* Filtres de colonnes mémorisés entre sessions (pas la recherche globale). */
  const FILTERS_KEY = "domopi_dev_filters";
  function saveFilters() {
    const o = {};
    document.querySelectorAll("#dev-filters [data-f]").forEach(el => {
      if (el.value) o[el.dataset.f] = el.value;
    });
    localStorage.setItem(FILTERS_KEY, JSON.stringify(o));
  }
  function restoreFilters() {
    let o = {};
    try { o = JSON.parse(localStorage.getItem(FILTERS_KEY)) || {}; } catch { }
    document.querySelectorAll("#dev-filters [data-f]").forEach(el => {
      el.value = o[el.dataset.f] ?? "";
      if (el.tagName === "SELECT" && el.selectedIndex < 0) el.value = "";
    });
  }

  function renderDevices() {
    const f = ($("#dev-filter").value || "").toLowerCase();
    let rows = devices.filter(d =>
      !f || [d.name, d.room, d.connector_name].join(" ").toLowerCase().includes(f));
    document.querySelectorAll("#dev-filters [data-f]").forEach(el => {
      const v = el.value, k = el.dataset.f;
      if (v === "") return;
      rows = rows.filter(d => {
        if (el.tagName !== "SELECT")
          return String(d[k] ?? "").toLowerCase().includes(v.toLowerCase());
        if (k === "kind") return d.kind === v;
        if (k === "scale_id") return v === "none" ? !d.scale_id : d.scale_id === +v;
        return (d[k] ? "1" : "0") === v;
      });
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
      <td><input type="checkbox" data-k="hidden" ${d.hidden ? "checked" : ""}
           title="Ne plus proposer ce périphérique pour les nouveaux widgets"></td>
      <td><input type="text" data-k="name" value="${esc(d.name)}" style="min-width:130px"></td>
      <td>${esc(d.room)}</td><td>${esc(d.connector_name)}</td>
      <td>${d.kind === "actuator" ? "sortie" : "capteur"}</td>
      <td><input type="text" data-k="unit" value="${esc(d.unit)}" style="width:56px"></td>
      <td><input type="checkbox" data-k="controllable" ${d.controllable ? "checked" : ""}
           title="Pilotage autorisé (sorties, ou capteurs virtuels d'une box)"></td>
      <td><select data-k="scale_id" title="Échelle de pilotage proportionnel"
             style="min-width:90px"><option value="">aucune</option>${scales.map(s =>
             `<option value="${s.id}" ${s.id === d.scale_id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></td>
      <td><img data-pick="icon_on" src="${d.icon_on ? "/static/icons/" + d.icon_on : ""}"
           alt="on" title="icône état actif" style="width:26px;height:26px;cursor:pointer;background:var(--panel-2);border-radius:5px;padding:2px">
          <img data-pick="icon_off" src="${d.icon_off ? "/static/icons/" + d.icon_off : ""}"
           alt="off" title="icône état inactif" style="width:26px;height:26px;cursor:pointer;background:var(--panel-2);border-radius:5px;padding:2px"></td>
      </tr>`).join("") || `<tr><td colspan="10" class="muted">Aucun périphérique.
        Utilisez « Découvrir les périphériques » sur un contrôleur.</td></tr>`;

    $("#dev-body").querySelectorAll("tr[data-id]").forEach(tr => {
      const d = devices.find(x => x.id === +tr.dataset.id);
      const save = async patch => {
        Object.assign(d, patch);
        await api(`/api/devices/${d.id}`, { method: "PUT", body: JSON.stringify(d) });
      };
      tr.querySelectorAll("input[data-k], select[data-k]").forEach(inp => inp.onchange = () => {
        const patch = { [inp.dataset.k]: inp.type === "checkbox" ? inp.checked : inp.value };
        if (inp.dataset.k === "scale_id") {
          const sid = inp.value ? +inp.value : null;
          patch.scale_id = sid;
          const s = scales.find(x => x.id === sid);
          d.scale_name = s ? s.name : "";
          // L'unité de l'échelle (si précisée) est recopiée sur le
          // périphérique — simple transfert au moment du choix, sans liaison.
          if (s && s.unit) {
            patch.unit = s.unit;
            tr.querySelector('input[data-k="unit"]').value = s.unit;
          }
        }
        save(patch).then(() => toast("Enregistré")).catch(e => toast(e.message));
      });
      tr.querySelectorAll("img[data-pick]").forEach(img => img.onclick = () =>
        pickIcon(chosen => save({ [img.dataset.pick]: chosen }).then(() => {
          img.src = chosen ? "/static/icons/" + chosen : ""; toast("Icône mise à jour");
        })));
    });
  }
  $("#dev-filter").oninput = renderDevices;
  document.querySelectorAll("#dev-filters [data-f]").forEach(el =>
    el.oninput = () => { saveFilters(); renderDevices(); });
  document.querySelectorAll("#dev-head th[data-sort]").forEach(th => th.onclick = () => {
    const k = th.dataset.sort;
    devSort = devSort.key === k ? { key: k, dir: -devSort.dir } : { key: k, dir: 1 };
    document.querySelectorAll("#dev-head .arrow").forEach(a => a.remove());
    th.insertAdjacentHTML("beforeend",
      `<span class="arrow">${devSort.dir > 0 ? " ▲" : " ▼"}</span>`);
    renderDevices();
  });

  /* La dernière icône choisie est mémorisée : bouton de reprise rapide +
     surbrillance dans la galerie (pratique pour équiper toute une série de
     périphériques de la même icône). */
  const LAST_ICON_KEY = "domopi_last_icon";
  function pickIcon(cb) {
    const last = localStorage.getItem(LAST_ICON_KEY) || "";
    const hasLast = last && icons.includes(last);
    const choose = i => { if (i) localStorage.setItem(LAST_ICON_KEY, i); cb(i); };
    dialog(`<h2 style="margin-top:0">Choisir une icône</h2>
      <div class="icon-pick" id="pick">${icons.map(i =>
        `<img src="/static/icons/${i}" title="${i}" data-i="${i}"
          ${i === last ? 'class="sel"' : ""}>`).join("")}</div>
      <div class="row" style="margin-top:.8rem">
        <div class="fix"><button class="btn" id="pick-none">Aucune icône</button></div>
        <div class="fix"><button class="btn" id="pick-cancel">Annuler</button></div>
        ${hasLast ? `<div class="fix" style="margin-left:auto">
          <button class="btn" id="pick-last" autofocus><img src="/static/icons/${last}"
            style="width:22px;height:22px;vertical-align:-5px;margin-right:.35rem">
            Reprendre la dernière icône</button></div>` : ""}</div>`, dlg => {
      dlg.querySelectorAll("#pick img").forEach(img =>
        img.onclick = () => { choose(img.dataset.i); dlg.close(); });
      if (hasLast) {
        dlg.querySelector("#pick-last").onclick = () => { choose(last); dlg.close(); };
        dlg.querySelector("#pick img.sel")?.scrollIntoView({ block: "center" });
      }
      dlg.querySelector("#pick-none").onclick = () => { cb(""); dlg.close(); };
      dlg.querySelector("#pick-cancel").onclick = () => dlg.close();
    });
  }

  /* ============================================ échelles de pilotage */
  async function loadScales() {
    scales = await api("/api/scales");
    // Filtre de la colonne « Échelle » du tableau des périphériques.
    const sel = document.querySelector('#dev-filters [data-f="scale_id"]');
    const cur = sel.value;
    sel.innerHTML = `<option value="">toutes</option><option value="none">aucune</option>` +
      scales.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
    sel.value = cur;
    if (sel.selectedIndex < 0) sel.value = "";
    renderScales();
  }

  function renderScales() {
    const box = $("#scales-list");
    box.innerHTML = scales.length ? "" :
      "<p class='muted'>Aucune échelle définie pour l'instant.</p>";
    for (const s of scales) {
      const div = document.createElement("div");
      div.className = "card"; div.style.marginBottom = ".5rem"; div.style.minHeight = "0";
      const n = devices.filter(d => d.scale_id === s.id).length;
      div.innerHTML = `<div class="row">
        <div><b>${esc(s.name)}</b> <span class="muted">— plage ${s.vmin} à
          ${s.vmax}${s.unit ? " " + esc(s.unit) : ""},
          pas ${s.step}${s.hide_slider ? ", barre masquée" : ""}${s.stops.length
          ? `, ${s.stops.length} boutons` : ""} · ${n} périphérique${n > 1 ? "s" : ""}</span></div>
        <div class="fix"><button class="btn sm" data-a="edit">Modifier</button></div>
        <div class="fix"><button class="btn sm danger" data-a="del">Supprimer</button></div></div>`;
      div.querySelector("[data-a=edit]").onclick = () => scaleForm(s);
      div.querySelector("[data-a=del]").onclick = async () => {
        if (!confirm(`Supprimer l'échelle « ${s.name} » ?` +
          (n ? `\n${n} périphérique(s) repasseront en pilotage tout-ou-rien.` : ""))) return;
        await api(`/api/scales/${s.id}`, { method: "DELETE" });
        await loadScales(); loadDevices();
      };
      box.appendChild(div);
    }
  }

  function scaleForm(s) {
    const stopRow = st => `<div class="stop-row">
      <input type="number" step="any" class="st-v" placeholder="valeur" value="${st.value ?? ""}">
      <input type="text" class="st-l" placeholder="texte (optionnel)" value="${esc(st.label || "")}">
      <img class="st-i" alt="" title="Icône (optionnelle) — cliquer pour choisir"
        src="${st.icon ? "/static/icons/" + st.icon : ""}">
      <button type="button" class="btn sm st-x" title="Retirer cette valeur">✕</button></div>`;
    dialog(`<h2 style="margin-top:0">${s.id ? "Modifier l'échelle" : "Nouvelle échelle"}</h2>
      <div class="row">
        <div><label>Nom</label><input id="sc-name" type="text" value="${esc(s.name || "")}"></div>
        <div style="flex:0 0 150px;min-width:0"><label title="Recopiée dans l'unité du périphérique
          au moment du choix de l'échelle">Unité (optionnelle)</label>
          <input id="sc-unit" type="text" placeholder="%, °C…" value="${esc(s.unit || "")}"></div>
      </div>
      <div class="row">
        <div><label>Minimum</label><input id="sc-min" type="number" step="any" value="${s.vmin ?? 0}"></div>
        <div><label>Maximum</label><input id="sc-max" type="number" step="any" value="${s.vmax ?? 100}"></div>
        <div><label>Résolution (cran)</label><input id="sc-step" type="number" step="any" min="0" value="${s.step ?? 1}"></div>
        <div><label>Auto-validation (s)</label><input id="sc-delay" type="number" step="0.1" min="0" value="${s.send_delay_s ?? 1.5}"></div>
      </div>
      <label><input type="checkbox" id="sc-hide" ${s.hide_slider ? "checked" : ""} style="width:auto">
        Masquer la barre de réglage (boutons seuls)</label>
      <label><input type="checkbox" id="sc-toggle" ${(s.toggle_click ?? 1) ? "checked" : ""} style="width:auto">
        Clic court = marche/arrêt (sinon le clic ouvre directement le réglage)</label>
      <label>Série de valeurs (boutons du réglage — 2 à 20, ou aucune) :
        valeur obligatoire, texte et icône optionnels</label>
      <div id="sc-stops">${(s.stops || []).map(stopRow).join("")}</div>
      <div class="row" style="margin-top:.3rem">
        <div class="fix"><button type="button" class="btn sm" id="sc-addstop">+ Ajouter une valeur</button></div>
      </div>
      <div id="sc-gallery" hidden style="margin-top:.4rem">
        <div class="row" style="margin-bottom:.3rem">
          <div class="fix"><button type="button" class="btn sm" id="sc-noicon">Sans icône</button></div>
        </div>
        <div class="icon-pick">${icons.map(i =>
          `<img src="/static/icons/${i}" data-i="${i}" title="${i}">`).join("")}</div>
      </div>
      <div class="row" style="margin-top:1rem">
        <button class="btn primary" id="sc-save">Enregistrer</button>
        <button class="btn" id="sc-cancel">Annuler</button></div>`, dlg => {
      const stopsBox = dlg.querySelector("#sc-stops");
      const gallery = dlg.querySelector("#sc-gallery");
      let iconTarget = null;
      const wireRow = row => {
        const img = row.querySelector(".st-i");
        img.onclick = () => { iconTarget = row; gallery.hidden = false; };
        row.querySelector(".st-x").onclick = () => row.remove();
      };
      stopsBox.querySelectorAll(".stop-row").forEach((row, i) => {
        row.dataset.icon = (s.stops || [])[i]?.icon || "";
        wireRow(row);
      });
      dlg.querySelector("#sc-addstop").onclick = () => {
        if (stopsBox.children.length >= 20) return toast("20 valeurs maximum");
        stopsBox.insertAdjacentHTML("beforeend", stopRow({}));
        const row = stopsBox.lastElementChild;
        row.dataset.icon = ""; wireRow(row);
        row.querySelector(".st-v").focus();
      };
      dlg.querySelectorAll("#sc-gallery .icon-pick img").forEach(img => img.onclick = () => {
        if (iconTarget) {
          iconTarget.dataset.icon = img.dataset.i;
          iconTarget.querySelector(".st-i").src = "/static/icons/" + img.dataset.i;
        }
        gallery.hidden = true;
      });
      dlg.querySelector("#sc-noicon").onclick = () => {
        if (iconTarget) { iconTarget.dataset.icon = ""; iconTarget.querySelector(".st-i").src = ""; }
        gallery.hidden = true;
      };
      dlg.querySelector("#sc-cancel").onclick = () => dlg.close();
      dlg.querySelector("#sc-save").onclick = async () => {
        const stops = [];
        for (const row of stopsBox.querySelectorAll(".stop-row")) {
          const v = row.querySelector(".st-v").value.trim();
          if (v === "") return toast("Chaque valeur de la série doit être renseignée");
          stops.push({ value: parseFloat(v.replace(",", ".")),
                       label: row.querySelector(".st-l").value.trim(),
                       icon: row.dataset.icon || "" });
        }
        if (stops.length === 1) return toast("Prévoyez au moins 2 valeurs, ou aucune");
        const body = {
          name: dlg.querySelector("#sc-name").value.trim(),
          unit: dlg.querySelector("#sc-unit").value.trim(),
          vmin: +dlg.querySelector("#sc-min").value,
          vmax: +dlg.querySelector("#sc-max").value,
          step: +dlg.querySelector("#sc-step").value,
          send_delay_s: +dlg.querySelector("#sc-delay").value,
          hide_slider: dlg.querySelector("#sc-hide").checked,
          toggle_click: dlg.querySelector("#sc-toggle").checked,
          stops };
        try {
          if (s.id) await api(`/api/scales/${s.id}`, { method: "PUT", body: JSON.stringify(body) });
          else await api("/api/scales", { method: "POST", body: JSON.stringify(body) });
        } catch (e) { return toast(e.message); }
        dlg.close(); toast("Échelle enregistrée");
        await loadScales(); loadDevices();
      };
    });
  }
  $("#scale-add").onclick = () => scaleForm({
    unit: "", vmin: 0, vmax: 100, step: 1, send_delay_s: 1.5,
    hide_slider: 0, toggle_click: 1, stops: [] });

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
          pageForm({ parent_id: p.id, title: "", background: "", dual_layout: 0,
                     sort_order: nextPageOrder(p.id) });
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
  /* Ordre par défaut d'une nouvelle page : max + 1 parmi ses pages sœurs
     (pages racines pour une racine, sous-pages du même parent sinon). */
  const nextPageOrder = parentId => {
    const sibs = pages.filter(x => x.parent_id === parentId);
    return sibs.length ? Math.max(...sibs.map(x => x.sort_order || 0)) + 1 : 0;
  };
  $("#page-add-root").onclick = () =>
    pageForm({ parent_id: null, title: "", background: "", dual_layout: 0,
               sort_order: nextPageOrder(null) });

  function pageForm(p) {
    const parentOpts = [`<option value="">— racine —</option>`].concat(
      pages.filter(x => x.id !== p.id).map(x =>
        `<option value="${x.id}" ${x.id === p.parent_id ? "selected" : ""}>${esc(x.title)}</option>`)).join("");
    const bgOpts = [`<option value="">— aucun / couleur ci-dessous —</option>`].concat(
      backgrounds.map(b => `<option ${p.background === b ? "selected" : ""}>${b}</option>`)).join("");
    dialog(`<h2 style="margin-top:0">${p.id ? "Modifier la page" : "Nouvelle page"}</h2>
      <label>Titre</label><input id="pf-title" type="text" value="${esc(p.title)}">
      <label>Page parente</label><select id="pf-parent">${parentOpts}</select>
      <label>Icône (mini dans la barre du haut, en fond de la tuile de sous-vue)</label>
      <div class="row">
        <div class="fix"><img id="pf-icon-prev" alt=""
          src="${p.icon ? "/static/icons/" + esc(p.icon) : ""}"
          style="width:34px;height:34px;background:var(--panel-2);border-radius:6px;padding:3px"></div>
        <div class="fix"><button type="button" class="btn" id="pf-icon-btn">Choisir…</button></div>
        <div class="fix"><button type="button" class="btn" id="pf-icon-clear">Aucune</button></div>
      </div>
      <div class="icon-pick" id="pf-icon-gallery" hidden style="margin-top:.4rem">${icons.map(i =>
        `<img src="/static/icons/${i}" data-i="${i}" title="${i}">`).join("")}</div>
      <label>Fond : image</label><select id="pf-bgimg">${bgOpts}</select>
      <label>Fond : couleur CSS (ex. #1a2430) — utilisé si aucune image</label>
      <input id="pf-bgcol" type="text" value="${p.background && p.background.startsWith("#") ? esc(p.background) : ""}">
      <label title="Affichage par ordre croissant : le numéro le plus faible est placé
        en premier. Sur la page parente, la tuile de cette sous-page partage cette
        numérotation avec les widgets.">Ordre d'affichage (petit = en premier)</label>
      <input id="pf-order" type="number" value="${p.sort_order || 0}">
      <label><input type="checkbox" id="pf-dual" ${p.dual_layout ? "checked" : ""} style="width:auto">
        Double rendu smartphone / PC (chaque widget peut cibler un des deux rendus)</label>
      <div class="row" style="margin-top:1rem">
        <button class="btn primary" id="pf-save">Enregistrer</button>
        <button class="btn" id="pf-cancel">Annuler</button></div>`, dlg => {
      let pfIcon = p.icon || "";
      dlg.querySelector("#pf-icon-btn").onclick = () => {
        const g = dlg.querySelector("#pf-icon-gallery"); g.hidden = !g.hidden;
      };
      dlg.querySelector("#pf-icon-clear").onclick = () => {
        pfIcon = ""; dlg.querySelector("#pf-icon-prev").src = "";
        dlg.querySelector("#pf-icon-gallery").hidden = true;
      };
      dlg.querySelectorAll("#pf-icon-gallery img").forEach(img => img.onclick = () => {
        pfIcon = img.dataset.i;
        dlg.querySelector("#pf-icon-prev").src = "/static/icons/" + pfIcon;
        dlg.querySelector("#pf-icon-gallery").hidden = true;
      });
      dlg.querySelector("#pf-cancel").onclick = () => dlg.close();
      dlg.querySelector("#pf-save").onclick = async () => {
        const body = {
          title: dlg.querySelector("#pf-title").value || "Sans titre",
          parent_id: dlg.querySelector("#pf-parent").value ? +dlg.querySelector("#pf-parent").value : null,
          background: dlg.querySelector("#pf-bgimg").value || dlg.querySelector("#pf-bgcol").value,
          icon: pfIcon,
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
      <td>${{ device: "périphérique", graph: "graphe", pagelink: "lien de page", label: "texte", weblink: "page web" }[w.wtype] || w.wtype}</td>
      <td>${esc(w.device_name || w.target_title || w.options.text || w.options.url || "")}</td>
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
    // Nouveau widget placé après les widgets existants : ordre max + 1.
    // Volontairement sans regarder les tuiles de sous-pages : elles sont en
    // général placées très en début (-20) ou très en fin (+20), et une
    // collision d'ordre est sans gravité alors que suivre leur index
    // provoquerait de gros sauts de numérotation.
    $("#we-add").onclick = () => widgetForm({ page_id: page.id, wtype: "device",
      layout: "both",
      sort_order: ws.length ? Math.max(...ws.map(w => w.sort_order || 0)) + 1 : 0,
      options: {} });
    $("#widgets-editor").scrollIntoView({ behavior: "smooth" });
  }

  function widgetForm(w) {
    // Périphériques proposés : les non masqués, triés par nom — plus celui
    // déjà affecté au widget, même masqué (les widgets existants le gardent).
    const devList = devices
      .filter(d => !d.hidden || d.id === w.device_id)
      .sort((a, b) => String(a.name).localeCompare(String(b.name), "fr",
                                                   { sensitivity: "base" }));
    const devOpts = (list, selected) => list.map(d =>
      `<option value="${d.id}" ${d.id === selected ? "selected" : ""}>${esc(d.name)} (${esc(d.room || d.connector_name)})</option>`).join("");
    const pageOpts = pages.map(p =>
      `<option value="${p.id}" ${p.id === w.target_page_id ? "selected" : ""}>${esc(p.title)}</option>`).join("");
    dialog(`<h2 style="margin-top:0">${w.id ? "Modifier le widget" : "Nouveau widget"}</h2>
      <label>Type</label><select id="wf-type">
        <option value="device" ${w.wtype === "device" ? "selected" : ""}>Périphérique (icône + valeur)</option>
        <option value="graph" ${w.wtype === "graph" ? "selected" : ""}>Graphe (courbes historiques)</option>
        <option value="pagelink" ${w.wtype === "pagelink" ? "selected" : ""}>Lien vers une page</option>
        <option value="label" ${w.wtype === "label" ? "selected" : ""}>Texte libre</option>
        <option value="weblink" ${w.wtype === "weblink" ? "selected" : ""}>Page web externe (encapsulée)</option></select>
      <div id="wf-web">
        <label>URL de la page (ex. http://192.168.1.202/)</label>
        <input id="wf-url" type="text" value="${esc(w.options.url || "")}">
        <div class="row">
          <div><label>Utilisateur (si la page demande une connexion)</label>
            <input id="wf-authu" type="text" value="${esc(w.options.auth_user || "")}"></div>
          <div><label>Mot de passe</label>
            <input id="wf-authp" type="password" value="${esc(w.options.auth_pass || "")}"></div>
        </div>
        <p class="muted" style="margin:.2rem 0 .6rem;font-size:.85rem">
          Laissés vides, la boîte de connexion du navigateur s'affichera si la
          page l'exige ; renseignés, la connexion est faite par le serveur
          (identifiants jamais transmis aux lecteurs).</p>
        <label>Icône de la tuile (optionnelle, derrière le 🌐)</label>
        <div class="row">
          <div class="fix"><img id="wf-icon-prev" alt=""
            src="${w.options.icon ? "/static/icons/" + esc(w.options.icon) : ""}"
            style="width:34px;height:34px;background:var(--panel-2);border-radius:6px;padding:3px"></div>
          <div class="fix"><button type="button" class="btn" id="wf-icon-btn">Choisir…</button></div>
          <div class="fix"><button type="button" class="btn" id="wf-icon-clear">Aucune</button></div>
        </div>
        <div class="icon-pick" id="wf-icon-gallery" hidden style="margin-top:.4rem">${icons.map(i =>
          `<img src="/static/icons/${i}" data-i="${i}" title="${i}">`).join("")}</div>
      </div>
      <div id="wf-dev"><label>Périphérique</label>
        <input id="wf-devsearch" type="text" placeholder="Recherche rapide (nom, pièce, contrôleur)…">
        <select id="wf-device" style="margin-top:.3rem">${devOpts(devList, w.device_id)}</select></div>
      <div id="wf-page"><label>Page cible</label><select id="wf-target">${pageOpts}</select></div>
      <div id="wf-text"><label>Texte</label><input id="wf-textv" type="text" value="${esc(w.options.text || "")}"></div>
      <label>Libellé affiché (optionnel)</label>
      <input id="wf-label" type="text" value="${esc(w.options.label || "")}">
      <div class="row">
        <div><label>Rendu</label><select id="wf-layout">
          <option value="both" ${w.layout === "both" ? "selected" : ""}>Smartphone et PC</option>
          <option value="mobile" ${w.layout === "mobile" ? "selected" : ""}>Smartphone uniquement</option>
          <option value="desktop" ${w.layout === "desktop" ? "selected" : ""}>PC uniquement</option></select></div>
        <div><label title="Affichage par ordre croissant : le numéro le plus faible est
          placé en premier (en haut de la page). Numérotation commune avec les tuiles
          de sous-pages de la même page.">Ordre (petit = en haut)</label>
          <input id="wf-order" type="number" value="${w.sort_order || 0}"></div>
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
        dlg.querySelector("#wf-web").hidden = t !== "weblink";
      };
      dlg.querySelector("#wf-type").onchange = sync; sync();
      let wfIcon = w.options.icon || "";
      dlg.querySelector("#wf-icon-btn").onclick = () => {
        const g = dlg.querySelector("#wf-icon-gallery"); g.hidden = !g.hidden;
      };
      dlg.querySelector("#wf-icon-clear").onclick = () => {
        wfIcon = ""; dlg.querySelector("#wf-icon-prev").src = "";
        dlg.querySelector("#wf-icon-gallery").hidden = true;
      };
      dlg.querySelectorAll("#wf-icon-gallery img").forEach(img => img.onclick = () => {
        wfIcon = img.dataset.i;
        dlg.querySelector("#wf-icon-prev").src = "/static/icons/" + wfIcon;
        dlg.querySelector("#wf-icon-gallery").hidden = true;
      });
      dlg.querySelector("#wf-devsearch").oninput = ev => {
        const q = ev.target.value.toLowerCase();
        const sel = dlg.querySelector("#wf-device");
        const cur = +sel.value;
        const list = devList.filter(d => !q ||
          [d.name, d.room, d.connector_name].join(" ").toLowerCase().includes(q));
        sel.innerHTML = devOpts(list, list.some(d => d.id === cur) ? cur
                                      : (list[0] || {}).id);
      };
      dlg.querySelector("#wf-cancel").onclick = () => dlg.close();
      dlg.querySelector("#wf-save").onclick = async () => {
        const t = dlg.querySelector("#wf-type").value;
        const body = { wtype: t, layout: dlg.querySelector("#wf-layout").value,
          sort_order: +dlg.querySelector("#wf-order").value || 0,
          device_id: (t === "device" || t === "graph")
            ? (+dlg.querySelector("#wf-device").value || null) : null,
          target_page_id: t === "pagelink" ? +dlg.querySelector("#wf-target").value : null,
          options: { label: dlg.querySelector("#wf-label").value,
                     text: dlg.querySelector("#wf-textv").value,
                     range_s: +dlg.querySelector("#wf-rangev").value } };
        if (t === "weblink") {
          const u = dlg.querySelector("#wf-url").value.trim();
          if (!/^https?:\/\//.test(u)) { toast("URL invalide : elle doit commencer par http:// ou https://"); return; }
          body.options.url = u;
          body.options.icon = wfIcon;
          body.options.auth_user = dlg.querySelector("#wf-authu").value.trim();
          body.options.auth_pass = dlg.querySelector("#wf-authp").value;
        }
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

  /* ============================================ onglets */
  const TABS = ["pages", "devices", "params", "icons", "general"];
  function showTab(name) {
    if (!TABS.includes(name)) name = "pages";
    document.querySelectorAll("#tabs .tab").forEach(b =>
      b.classList.toggle("active", b.dataset.tab === name));
    TABS.forEach(t => $("#tab-" + t).hidden = t !== name);
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  }
  document.querySelectorAll("#tabs .tab").forEach(b =>
    b.onclick = () => showTab(b.dataset.tab));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));

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
    await loadScales();          // avant les périphériques (noms d'échelles, filtre)
    restoreFilters();            // avant le premier rendu du tableau
    await Promise.all([loadConnectors(), loadDevices(), loadPages(), loadUsers()]);
  })();
})();
