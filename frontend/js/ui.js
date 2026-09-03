/* =========================================================================
   DOM olusturma ve guncelleme yardimcilari.

   Kartlar, tablo satirlari ve SVG gorseller yalnizca bir kez olusturulur;
   telemetri akisinda sadece degisen hucre degerleri yazilir (10 Hz'de
   30 arac icin DOM yeniden olusturmak pahalidir).
   ========================================================================= */
(function (global) {
  "use strict";

  const GAUGE_ARC_LENGTH = 251.3; // yarim daire, r = 80

  const el = (id) => document.getElementById(id);
  const fmt = (value, digits = 1) => Number(value || 0).toFixed(digits);
  const pct = (value) => `${Math.min(Math.max(Number(value) || 0, 0), 100)}%`;

  const MODE_LABEL = { manual: "MANUEL", auto: "OTO", idle: "ROLANTI" };

  /** Vitesi gosterge etiketine cevirir: -1 -> "R", 0 -> "N", 8 -> "8" */
  function gearLabel(gear, range) {
    if (range === "P") return "P";
    if (range === "N" || gear === 0) return "N";
    if (range === "R" || gear < 0) return "R";
    return String(gear);
  }

  /* ----------------------------------------------------------- filo grid */

  function renderFleet(container, brands, options) {
    const { search, brandId, onSelect } = options;
    const needle = (search || "").trim().toLocaleLowerCase("tr");
    const refs = new Map();
    container.textContent = "";

    brands.forEach((brand) => {
      if (brandId && brand.id !== brandId) return;

      const matching = brand.vehicles.filter((vehicle) => {
        if (!needle) return true;
        return `${brand.name} ${vehicle.model} ${vehicle.segment} ${vehicle.powertrain}`
          .toLocaleLowerCase("tr")
          .includes(needle);
      });
      if (matching.length === 0) return;

      const group = document.createElement("div");
      group.className = "brand-group";
      group.innerHTML = `
        <div class="brand-group__head">
          <span class="brand-group__dot" style="background:${brand.color}"></span>
          <span class="brand-group__name">${brand.name}</span>
          <span class="brand-group__country">${brand.country}</span>
        </div>
        <div class="cards"></div>`;

      const cards = group.querySelector(".cards");

      matching.forEach((vehicle) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "card";
        card.dataset.vehicleId = vehicle.id;
        card.style.borderLeftColor = brand.color;
        card.innerHTML = `
          <span class="card__art">
            ${global.J1939VehicleArt(vehicle)}
            <span class="card__gear" title="Vites (SPN 523)">N</span>
          </span>
          <span class="card__body">
            <span class="card__top">
              <span class="card__model">${vehicle.model}</span>
              <span class="card__led" data-state="off"></span>
            </span>
            <span class="card__id mono">${vehicle.can_id_hex} · SA ${vehicle.source_address_hex}</span>

            <span class="card__speed">
              <span class="card__speed-value">0.0</span>
              <span class="card__speed-unit">km/h</span>
              <span class="card__mode">MANUEL</span>
            </span>
            <span class="card__bar"><span style="width:0%"></span></span>

            <span class="card__pedals">
              <span class="pedal" title="Gaz pedali (SPN 91)">
                <b>GAZ</b><span class="pedal__track"><span class="pedal__fill pedal__fill--accel"></span></span>
              </span>
              <span class="pedal" title="Fren pedali (SPN 521)">
                <b>FREN</b><span class="pedal__track"><span class="pedal__fill pedal__fill--brake"></span></span>
              </span>
            </span>

            <span class="card__meters">
              <span class="meter" title="Batarya doluluk (SPN 5464)">
                <b>SOC</b>
                <span class="meter__track"><span class="meter__fill meter__fill--soc"></span></span>
                <i class="meter__value">—</i>
              </span>
              <span class="meter" title="Batarya sagligi (SPN 5465)">
                <b>SOH</b>
                <span class="meter__track"><span class="meter__fill meter__fill--soh"></span></span>
                <i class="meter__value">—</i>
              </span>
            </span>
          </span>`;
        card.addEventListener("click", () => onSelect(vehicle.id));
        cards.appendChild(card);

        refs.set(vehicle.id, {
          card,
          led: card.querySelector(".card__led"),
          speed: card.querySelector(".card__speed-value"),
          mode: card.querySelector(".card__mode"),
          bar: card.querySelector(".card__bar span"),
          gear: card.querySelector(".card__gear"),
          accel: card.querySelector(".pedal__fill--accel"),
          brakePedal: card.querySelector(".pedal__fill--brake"),
          soc: card.querySelector(".meter__fill--soc"),
          soh: card.querySelector(".meter__fill--soh"),
          socValue: card.querySelectorAll(".meter__value")[0],
          sohValue: card.querySelectorAll(".meter__value")[1],
        });
      });

      container.appendChild(group);
    });

    if (refs.size === 0) {
      container.innerHTML = '<p class="empty">Aramayla eslesen arac yok.</p>';
    }
    return refs;
  }

  function updateCard(ref, vehicleState, telemetry, maxSpeed, isSelected) {
    if (!ref) return;
    const online = vehicleState.online;
    const t = telemetry || {};
    const speed = online ? (telemetry ? t.speed : vehicleState.speed_kmh) : 0;

    ref.speed.textContent = fmt(speed);
    ref.mode.textContent = MODE_LABEL[vehicleState.mode] || vehicleState.mode;
    ref.bar.style.width = pct((speed / maxSpeed) * 100);
    ref.led.dataset.state = !online ? "off" : speed > 0.5 ? "moving" : "idle";

    ref.gear.textContent = online ? gearLabel(t.gear ?? vehicleState.gear,
                                              t.range ?? vehicleState.gear_range) : "—";
    ref.accel.style.width = online ? pct(t.accel ?? vehicleState.accel_pedal_pct) : "0%";
    ref.brakePedal.style.width = online ? pct(t.brake ?? vehicleState.brake_pedal_pct) : "0%";

    const soc = t.soc ?? vehicleState.soc_pct;
    const soh = t.soh ?? vehicleState.soh_pct;
    ref.soc.style.width = pct(soc);
    ref.soh.style.width = pct(soh);
    ref.soc.dataset.level = soc < 20 ? "low" : soc < 45 ? "mid" : "ok";
    ref.socValue.textContent = `${fmt(soc, 0)}%`;
    ref.sohValue.textContent = `${fmt(soh, 0)}%`;

    ref.card.classList.toggle("is-offline", !online);
    ref.card.classList.toggle("is-selected", isSelected);
  }

  /* ------------------------------------------------------ izleme tablosu */

  const MONITOR_COLUMNS = [
    "Arac", "CAN ID", "Hiz", "Gosterge", "Gaz", "Fren", "Vites", "Devir", "SOC",
    "SPN 84 Ham", "CCVS1 Veri Alani", "TX",
  ];

  function renderMonitor(tbody, vehicles, onSelect) {
    const refs = new Map();
    tbody.textContent = "";

    vehicles.forEach((vehicle) => {
      const row = document.createElement("tr");
      row.dataset.vehicleId = vehicle.id;
      row.innerHTML = `
        <td>${vehicle.display_name}</td>
        <td class="mono-cell">${vehicle.can_id_hex}</td>
        <td class="num speed-cell">0.0</td>
        <td><div class="mbar"><span style="width:0%"></span></div></td>
        <td class="num accel-cell">0</td>
        <td class="num brake-cell">0</td>
        <td class="num gear-cell">N</td>
        <td class="num rpm-cell">0</td>
        <td class="num soc-cell">—</td>
        <td class="num raw-cell">0</td>
        <td class="mono-cell data-cell">--</td>
        <td class="num tx-cell">0</td>`;
      row.addEventListener("click", () => onSelect(vehicle.id));
      tbody.appendChild(row);

      refs.set(vehicle.id, {
        row,
        speed: row.querySelector(".speed-cell"),
        bar: row.querySelector(".mbar span"),
        accel: row.querySelector(".accel-cell"),
        brake: row.querySelector(".brake-cell"),
        gear: row.querySelector(".gear-cell"),
        rpm: row.querySelector(".rpm-cell"),
        soc: row.querySelector(".soc-cell"),
        raw: row.querySelector(".raw-cell"),
        data: row.querySelector(".data-cell"),
        tx: row.querySelector(".tx-cell"),
      });
    });

    return refs;
  }

  function updateMonitorRow(ref, vehicleState, telemetry, maxSpeed, isSelected) {
    if (!ref) return;
    const online = vehicleState.online;
    const t = telemetry;
    const speed = online && t ? t.speed : 0;
    const ratio = Math.min(speed / maxSpeed, 1);

    ref.speed.textContent = fmt(speed);
    ref.bar.style.width = pct(ratio * 100);
    ref.bar.className = ratio > 0.9 ? "is-max" : ratio > 0.6 ? "is-fast" : "";
    ref.accel.textContent = online && t ? fmt(t.accel, 0) : "—";
    ref.brake.textContent = online && t ? fmt(t.brake, 0) : "—";
    ref.gear.textContent = online && t ? gearLabel(t.gear, t.range) : "—";
    ref.rpm.textContent = online && t ? Math.round(t.rpm).toLocaleString("tr-TR") : "—";
    ref.soc.textContent = online && t ? `${fmt(t.soc, 0)}%` : "—";
    ref.raw.textContent = online && t ? Math.round(speed * 256) : "—";
    ref.data.textContent = online && t ? t.dataHex : "— sessiz —";
    ref.tx.textContent = t ? t.tx.toLocaleString("tr-TR") : "0";
    ref.row.classList.toggle("is-offline", !online);
    ref.row.classList.toggle("is-selected", isSelected);
  }

  /* -------------------------------------------------------------- gosterge */

  function updateGauge(speed, target, maxSpeed) {
    const ratio = Math.min(Math.max(speed / maxSpeed, 0), 1);
    const arc = el("gauge-arc");
    arc.style.strokeDashoffset = String(GAUGE_ARC_LENGTH * (1 - ratio));
    arc.style.stroke = ratio > 0.9 ? "var(--danger)" : ratio > 0.6 ? "var(--warn)" : "var(--accent)";
    el("gauge-speed").textContent = fmt(speed);
    el("gauge-target").textContent = fmt(target, 0);
  }

  function updateReadouts(state, telemetry) {
    const t = telemetry || {};
    const gear = t.gear ?? state.gear;
    const range = t.range ?? state.gear_range;

    el("readout-gear").textContent = gearLabel(gear, range);
    el("readout-rpm").textContent = Math.round(t.rpm ?? state.engine_rpm).toLocaleString("tr-TR");
    el("readout-load").textContent = `${fmt(state.engine_load_pct, 0)}%`;

    const soc = t.soc ?? state.soc_pct;
    const soh = t.soh ?? state.soh_pct;
    el("batt-soc-fill").style.width = pct(soc);
    el("batt-soc-fill").dataset.level = soc < 20 ? "low" : soc < 45 ? "mid" : "ok";
    el("batt-soc-value").textContent = `${fmt(soc)}%`;
    el("batt-soh-fill").style.width = pct(soh);
    el("batt-soh-value").textContent = `${fmt(soh)}%`;
  }

  /* ------------------------------------------------------------- can log */

  function appendLogRow(logEl, entry, maxRows) {
    const row = document.createElement("div");
    row.className = "log__row";
    row.innerHTML = `
      <span class="log__time">${entry.time}</span>
      <span class="log__pgn" data-pgn="${entry.acronym}">${entry.acronym}</span>
      <span class="log__id">${entry.canId}</span>
      <span class="log__sep">#</span>
      <span class="log__data">${entry.data}</span>
      <span class="log__note">${entry.note}</span>`;

    const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
    logEl.appendChild(row);
    while (logEl.childElementCount > maxRows) logEl.removeChild(logEl.firstElementChild);
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  /* --------------------------------------------------- cerceve gorunumu */

  /** Secili aracin her PGN icin en son cercevesini listeler. */
  function renderFrameTable(container, messages) {
    container.textContent = "";
    const refs = new Map();

    messages.forEach((message) => {
      const row = document.createElement("div");
      row.className = "frame-row";
      row.innerHTML = `
        <span class="frame-row__pgn" data-pgn="${message.acronym}">${message.acronym}</span>
        <span class="frame-row__meta mono">${message.pgn_hex}</span>
        <code class="frame-row__value mono">—</code>`;
      row.title = `${message.name} · PGN ${message.pgn} · ${message.transmit_rate_ms} ms`;
      container.appendChild(row);
      refs.set(message.pgn, row.querySelector(".frame-row__value"));
    });

    return refs;
  }

  function updateFrameRow(node, frame) {
    if (!node) return;
    node.textContent = frame ? `${frame.can_id_hex}#${frame.data_hex}` : "—";
  }

  global.J1939Ui = {
    el,
    fmt,
    gearLabel,
    MONITOR_COLUMNS,
    renderFleet,
    updateCard,
    renderMonitor,
    updateMonitorRow,
    updateGauge,
    updateReadouts,
    appendLogRow,
    renderFrameTable,
    updateFrameRow,
  };
})(window);
