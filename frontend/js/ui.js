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
    // Yuklenemeyen fotograflar sessizce SVG cizime doner.
    global.J1939VehicleArt.bindFallbacks(container);
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

  /* -------------------------------------------------------------- sekmeler */

  function switchTab(tabName) {
    document.querySelectorAll(".tab").forEach((btn) =>
      btn.classList.toggle("is-active", btn.dataset.tab === tabName)
    );
    document.querySelectorAll("[data-tabpanel]").forEach((panel) => {
      panel.hidden = panel.dataset.tabpanel !== tabName;
    });
  }

  /* ------------------------------------------- DBC sinyal panelleri (sekmeler)

     Motor / Aktarma / Fren / Ortam sekmeleri asagidaki bildirimsel tanimdan
     uretilir. Her satir bir SPN'e karsilik gelir; DOM bir kez kurulur, telemetri
     akisinda yalnizca deger hucreleri guncellenir.
     ------------------------------------------------------------------------ */

  const TORQUE_MODE_LABELS = {
    0: "Rolanti", 1: "Pedal", 2: "Cruise", 3: "PTO", 4: "Yol Hizi",
    5: "ASR", 6: "Sanziman", 7: "ABS", 8: "Tork Limiti", 9: "Yuksek Hiz", 14: "Veri Yok",
  };
  const STARTER_MODE_LABELS = {
    0: "Mars Kapali", 1: "Mars Aktif", 2: "Calisiyor", 14: "Veri Yok",
  };
  const PTO_STATE_LABELS = {
    0: "Kapali", 1: "Bekliyor", 2: "Ayarlandi", 3: "Yavaslatma",
    4: "Hizlandirma", 5: "Devrede", 31: "Veri Yok",
  };

  /** Sinyal satiri: [durum anahtari, etiket, SPN, birim, ondalik] veya tip alani. */
  const SIGNAL_PANELS = {
    engine: [
      ["Motor Torku · EEC1 / EEC2", [
        ["driver_demand_torque_pct", "Surucu Talebi", 512, "%", 0],
        ["actual_engine_torque_pct", "Gercek Tork", 513, "%", 0],
        ["demand_engine_torque_pct", "Talep Edilen Tork", 2432, "%", 0],
        ["max_available_torque_pct", "Azami Kullanilabilir", 539, "%", 0],
        ["parasitic_losses_pct", "Parazitik Kayiplar", 1481, "%", 0],
        ["torque_mode", "Tork Modu", 899, "enum", TORQUE_MODE_LABELS],
        ["starter_mode", "Mars Modu", 1675, "enum", STARTER_MODE_LABELS],
      ]],
      ["Sicakliklar · ET1 / IC1", [
        ["coolant_temp_c", "Sogutma Suyu", 110, "°C", 1],
        ["oil_temp_c", "Motor Yagi", 175, "°C", 1],
        ["turbo_oil_temp_c", "Turbo Yagi", 176, "°C", 1],
        ["fuel_temp_c", "Yakit", 174, "°C", 1],
        ["intake_manifold_temp_c", "Emme Manifoldu", 105, "°C", 1],
        ["exhaust_gas_temp_c", "Egzoz Gazi", 173, "°C", 1],
      ]],
      ["Basinclar · EFLP1 / IC1", [
        ["oil_pressure_kpa", "Yag Basinci", 100, "kPa", 0],
        ["boost_pressure_kpa", "Turbo Basinci", 102, "kPa", 0],
        ["fuel_delivery_pressure_kpa", "Yakit Besleme", 94, "kPa", 0],
        ["coolant_pressure_kpa", "Sogutma Devresi", 109, "kPa", 0],
        ["particulate_trap_pressure_kpa", "Partikul Filtresi", 81, "kPa", 2],
        ["air_filter_diff_pressure_kpa", "Hava Filtresi", 107, "kPa", 2],
        ["coolant_filter_diff_pressure_kpa", "Su Filtresi", 112, "kPa", 2],
      ]],
      ["Seviye ve Yakit · LFE1 / EFLP1", [
        ["oil_level_pct", "Yag Seviyesi", 98, "%", 1],
        ["coolant_level_pct", "Sogutma Suyu Seviyesi", 111, "%", 1],
        ["fuel_rate_lph", "Yakit Tuketimi", 183, "L/h", 2],
        ["instant_fuel_economy_kmpl", "Anlik Ekonomi", 184, "km/L", 2],
        ["average_fuel_economy_kmpl", "Ortalama Ekonomi", 185, "km/L", 2],
        ["throttle_valve_pct", "Gaz Kelebegi", 51, "%", 1],
      ]],
    ],
    transmission: [
      ["Mil Devirleri · ETC1 / ETC2", [
        ["input_shaft_rpm", "Giris Mili", 161, "rpm", 0],
        ["output_shaft_rpm", "Cikis Mili", 191, "rpm", 0],
        ["gear_ratio", "Aktarma Orani", 526, "", 3],
        ["clutch_slip_pct", "Debriyaj Kaymasi", 522, "%", 1],
      ]],
      ["Durumlar · ETC1", [
        ["driveline_engaged", "Aktarma Devrede", 560, "bool"],
        ["torque_converter_lockup", "Konvertor Kilidi", 573, "bool"],
        ["shift_in_process", "Vites Degisiyor", 574, "bool"],
      ]],
    ],
    brakes: [
      ["Fren Talebi · EBC1", [
        ["total_brake_demand_pct", "Toplam Fren Talebi", 2911, "%", 1],
        ["foundation_brakes_in_use", "Servis Frenleri", 4251, "bool"],
      ]],
      ["ABS / ASR · EBC1", [
        ["abs_active", "ABS Aktif", 563, "bool"],
        ["abs_fully_operational", "ABS Tam Calisir", 575, "bool"],
        ["asr_engine_control", "ASR Motor Kontrolu", 561, "bool"],
        ["asr_brake_control", "ASR Fren Kontrolu", 562, "bool"],
        ["atc_asr_information", "ATC/ASR Bilgi", 1793, "bool"],
      ]],
      ["Uyarilar · EBC1", [
        ["ebs_red_warning", "EBS Kirmizi Uyari", 1438, "alarm"],
        ["ebs_amber_warning", "EBS Sari Uyari", 1439, "alarm"],
        ["trailer_connected", "Treyler Bagli", 1836, "bool"],
      ]],
    ],
    ambient: [
      ["Ortam Kosullari · AMB", [
        ["ambient_air_temp_c", "Dis Hava", 171, "°C", 1],
        ["cab_interior_temp_c", "Kabin Ici", 170, "°C", 1],
        ["road_surface_temp_c", "Yol Yuzeyi", 79, "°C", 1],
        ["air_inlet_temp_c", "Hava Girisi", 172, "°C", 1],
        ["barometric_pressure_kpa", "Barometrik Basinc", 108, "kPa", 1],
      ]],
      ["Gosterge Paneli · DD", [
        ["fuel_level_pct", "Yakit Deposu 1", 96, "%", 1],
        ["fuel_level2_pct", "Yakit Deposu 2", 38, "%", 1],
        ["washer_fluid_level_pct", "Cam Suyu", 80, "%", 1],
        ["cargo_ambient_temp_c", "Kargo Sicakligi", 169, "°C", 1],
        ["seat_belt_fastened", "Emniyet Kemeri", 1856, "bool"],
        ["exterior_light_on", "Dis Aydinlatma", 1883, "bool"],
        ["maintenance_lamp_on", "Bakim Lambasi", 1420, "alarm"],
      ]],
      ["Guc Cikisi · CCVS1 / HOURS", [
        ["pto_state", "PTO Durumu", 976, "enum", PTO_STATE_LABELS],
        ["pto_hours", "PTO Calisma Saati", 248, "h", 2],
      ]],
    ],
  };

  const WHEEL_LABELS = [
    ["On Sol", 905], ["On Sag", 906],
    ["Arka-1 Sol", 907], ["Arka-1 Sag", 908],
    ["Arka-2 Sol", 909], ["Arka-2 Sag", 910],
  ];

  /** Bir sinyal satirinin metnini ve durum sinifini uretir. */
  function formatSignal(value, unit, precision) {
    if (value === null || value === undefined) return { text: "—", state: "na" };
    if (unit === "bool" || unit === "alarm") {
      const on = Boolean(value);
      const state = unit === "alarm" ? (on ? "alarm" : "ok") : on ? "on" : "off";
      return { text: on ? "ACIK" : "KAPALI", state };
    }
    if (unit === "enum") {
      return { text: precision[value] ?? String(value), state: "text" };
    }
    const text = Number(value).toFixed(precision);
    return { text: unit ? `${text} ${unit}` : text, state: "value" };
  }

  /** Bir sekmenin DOM'unu kurar; key -> deger hucresi eslemesi dondurur. */
  function renderSignalPanel(container, groups) {
    container.textContent = "";
    const refs = new Map();

    groups.forEach(([title, rows]) => {
      const group = document.createElement("div");
      group.className = "signal-group";
      const heading = document.createElement("h4");
      heading.className = "signal-group__title";
      heading.textContent = title;
      group.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "signal-grid";
      rows.forEach(([key, label, spn, unit, precision]) => {
        const cell = document.createElement("div");
        cell.className = "signal";
        cell.innerHTML = `
          <span class="signal__label">${label}<i>SPN ${spn}</i></span>
          <span class="signal__value">—</span>`;
        grid.appendChild(cell);
        refs.set(key, { node: cell.querySelector(".signal__value"), unit, precision });
      });

      group.appendChild(grid);
      container.appendChild(group);
    });

    return refs;
  }

  /** Sinyal hucrelerini durum sozlugundeki degerlerle gunceller. */
  function updateSignalPanel(refs, data) {
    if (!refs || !data) return;
    refs.forEach((ref, key) => {
      const { text, state } = formatSignal(data[key], ref.unit, ref.precision);
      if (ref.node.textContent !== text) ref.node.textContent = text;
      if (ref.node.dataset.state !== state) ref.node.dataset.state = state;
    });
  }

  function renderWheelGrid(container) {
    container.textContent = "";
    const nodes = WHEEL_LABELS.map(([label, spn]) => {
      const cell = document.createElement("div");
      cell.className = "signal";
      cell.innerHTML = `
        <span class="signal__label">${label}<i>SPN ${spn}</i></span>
        <span class="signal__value">—</span>`;
      container.appendChild(cell);
      return cell.querySelector(".signal__value");
    });
    return nodes;
  }

  function updateWheelGrid(nodes, wheelSlip) {
    if (!nodes) return;
    const values = wheelSlip || [];
    nodes.forEach((node, index) => {
      const value = values[index];
      const text = value === undefined ? "—" : `${Number(value) >= 0 ? "+" : ""}${fmt(value, 2)} km/h`;
      if (node.textContent !== text) node.textContent = text;
      // Belirgin ayrisma (ABS/ASR) vurgulanir.
      node.dataset.state = Math.abs(Number(value) || 0) > 1.0 ? "alarm" : "value";
    });
  }

  /* ------------------------------------------------------- telematik okumalar */

  function updateTelematicsReadouts(vehicleState) {
    el("ro-lat").textContent = fmt(vehicleState.latitude, 5);
    el("ro-lon").textContent = fmt(vehicleState.longitude, 5);
    el("ro-odometer").textContent = `${fmt(vehicleState.odometer_km, 0)} km`;
  }

  /* ------------------------------------------------------------------ harita */

  let leafletMap = null;
  let leafletMarker = null;

  function ensureMap() {
    if (leafletMap || !global.L) return leafletMap;
    leafletMap = global.L.map("vehicle-map", { attributionControl: false, zoomControl: true })
      .setView([41.0, 29.0], 12);
    global.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
    }).addTo(leafletMap);
    leafletMarker = global.L.marker([41.0, 29.0]).addTo(leafletMap);
    return leafletMap;
  }

  function updateMap(lat, lon, label) {
    const map = ensureMap();
    if (!map) return;
    leafletMarker.setLatLng([lat, lon]);
    leafletMarker.bindTooltip(label || "", { permanent: false });
    map.panTo([lat, lon], { animate: true });
  }

  function invalidateMapSize() {
    if (leafletMap) leafletMap.invalidateSize();
  }

  /* --------------------------------------------------------------- ariza */

  function renderDtcList(container, activeDtcs) {
    if (!activeDtcs || activeDtcs.length === 0) {
      container.innerHTML = '<p class="empty">Aktif ariza yok.</p>';
      return;
    }
    container.innerHTML = activeDtcs
      .map((dtc) => `
        <div class="dtc-row is-${dtc.occurrence_count > 3 ? "critical" : "warning"}">
          <span class="dtc-row__spn">SPN ${dtc.spn}</span>
          <span class="dtc-row__name">${dtc.spn_name || "Bilinmeyen SPN"}</span>
          <span class="dtc-row__fmi">FMI ${dtc.fmi} · ${dtc.fmi_name || ""}</span>
          <span class="dtc-row__count">${dtc.occurrence_count}x</span>
        </div>`)
      .join("");
  }

  /* --------------------------------------------------------------- skor */

  const SCORE_RING_LENGTH = 326.7; // 2*pi*52

  function updateScorePanel(score, harshBrakeCount, overspeedCount) {
    const ratio = Math.min(Math.max(score / 100, 0), 1);
    const ring = el("score-ring");
    ring.style.strokeDasharray = String(SCORE_RING_LENGTH);
    ring.style.strokeDashoffset = String(SCORE_RING_LENGTH * (1 - ratio));
    ring.style.stroke = score >= 80 ? "var(--ok)" : score >= 50 ? "var(--warn)" : "var(--danger)";
    el("score-value").textContent = String(Math.round(score));
    el("score-harsh").textContent = String(harshBrakeCount);
    el("score-overspeed").textContent = String(overspeedCount);
  }

  function updateMaintenancePanel(odometerKm, serviceIntervalKm) {
    const sinceService = odometerKm % serviceIntervalKm;
    const remaining = serviceIntervalKm - sinceService;
    const ratio = sinceService / serviceIntervalKm;
    el("maint-remaining").textContent = `${fmt(remaining, 0)} km`;
    const fill = el("maint-bar-fill");
    fill.style.width = `${ratio * 100}%`;
    fill.classList.toggle("is-warning", ratio > 0.85);
  }

  /* ---------------------------------------------------------------- lightbox */

  function openLightbox(src, alt) {
    const img = el("lightbox-img");
    img.src = src;
    img.alt = alt || "";
    el("lightbox").hidden = false;
  }

  function closeLightbox() {
    el("lightbox").hidden = true;
    el("lightbox-img").src = "";
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
    switchTab,
    SIGNAL_PANELS,
    renderSignalPanel,
    updateSignalPanel,
    renderWheelGrid,
    updateWheelGrid,
    updateTelematicsReadouts,
    updateMap,
    invalidateMapSize,
    renderDtcList,
    updateScorePanel,
    updateMaintenancePanel,
    openLightbox,
    closeLightbox,
  };
})(window);
