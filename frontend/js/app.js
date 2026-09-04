/* =========================================================================
   Uygulama cekirdegi: durum yonetimi, WebSocket akisi, olay baglama ve
   motor sesi surusu.
   ========================================================================= */
(function (global) {
  "use strict";

  const {
    el, fmt, gearLabel, renderFleet, updateCard, renderMonitor, updateMonitorRow,
    updateGauge, updateReadouts, appendLogRow, renderFrameTable, updateFrameRow,
    switchTab, updateTelematicsReadouts, updateMap, invalidateMapSize, renderDtcList,
    updateScorePanel, updateMaintenancePanel, openLightbox, closeLightbox,
  } = global.J1939Ui;

  const LOG_MAX_ROWS = 300;
  const LOG_ALL_EVERY_N_TICKS = 10; // tum filo modunda saniyede ~1 tur
  const SERVICE_INTERVAL_KM = 15000;
  const HARSH_BRAKE_SPEED_THRESHOLD = 40; // km/h ustunde frene basmak "ani fren" sayilir

  // Sikistirilmis telemetri satirinin alan sirasi (backend _compact_rows ile ayni)
  const T = { ID: 0, SPEED: 1, ACCEL: 2, BRAKE: 3, GEAR: 4, RANGE: 5,
              RPM: 6, SOC: 7, SOH: 8, HEX: 9, TX: 10 };

  const state = {
    brands: [],
    vehicles: new Map(),   // id -> arac tanimi
    states: new Map(),     // id -> simulator durumu
    telemetry: new Map(),  // id -> cozulmus telemetri satiri
    driverStats: new Map(), // id -> { harshBrakes, overspeedCount, score, prevBrake, wasOverspeed }
    messages: [],          // PGN tanimlari
    meta: { max_speed_kmh: 180, tick_ms: 100 },
    selectedId: null,
    activeTab: "telemetry",
    filters: { search: "", brand: "" },
    log: { paused: false, all: false },
    dirty: false,
  };

  const cardRefs = new Map();
  let monitorRefs = new Map();
  let frameRefs = new Map();

  const socket = new global.J1939Socket(global.J1939Api.WS_URL);
  const audio = new global.J1939EngineAudio();

  /* ------------------------------------------------------------ yardimci */

  const selectedVehicle = () => state.vehicles.get(state.selectedId);
  const selectedState = () => state.states.get(state.selectedId);

  function send(payload) {
    if (!socket.send(payload)) {
      console.warn("WebSocket kapali, komut gonderilemedi:", payload);
    }
  }

  function timestamp() {
    const now = new Date();
    return now.toLocaleTimeString("tr-TR", { hour12: false }) +
      "." + String(now.getMilliseconds()).padStart(3, "0");
  }

  /* -------------------------------------------------------- ilk yukleme */

  function applySnapshot(message) {
    state.brands = message.brands;
    state.meta = message.meta;
    state.messages = message.meta.messages || [];

    state.vehicles.clear();
    message.brands.forEach((brand) =>
      brand.vehicles.forEach((vehicle) => state.vehicles.set(vehicle.id, vehicle))
    );
    Object.entries(message.states).forEach(([id, vehicleState]) =>
      state.states.set(id, vehicleState)
    );

    el("fleet-count").textContent = `${state.vehicles.size} arac`;
    el("footer-endpoint").textContent = global.J1939Api.endpointLabel;
    el("footer-messages").textContent =
      state.messages.map((m) => `${m.acronym} ${m.pgn_hex}`).join(" · ");

    const filter = el("brand-filter");
    if (filter.childElementCount <= 1) {
      message.brands.forEach((brand) => {
        const option = document.createElement("option");
        option.value = brand.id;
        option.textContent = brand.name;
        filter.appendChild(option);
      });
    }

    el("speed-slider").max = String(state.meta.max_speed_kmh);
    el("speed-input").max = String(state.meta.max_speed_kmh);

    frameRefs = renderFrameTable(el("frame-list"), state.messages);
    rebuildFleet();
    monitorRefs = renderMonitor(el("monitor-body"), [...state.vehicles.values()], selectVehicle);

    if (!state.selectedId) selectVehicle(state.vehicles.keys().next().value);
    else applySelection();

    if (message.stats) updateStats(message.stats);
    state.dirty = true;
  }

  function rebuildFleet() {
    cardRefs.clear();
    const refs = renderFleet(el("fleet"), state.brands, {
      search: state.filters.search,
      brandId: state.filters.brand,
      onSelect: selectVehicle,
    });
    refs.forEach((ref, id) => cardRefs.set(id, ref));
    state.dirty = true;
  }

  /* ------------------------------------------------------------- telemetri */

  function onTelemetry(message) {
    const transmitting = new Set();

    message.t.forEach((row) => {
      transmitting.add(row[T.ID]);
      state.telemetry.set(row[T.ID], {
        speed: row[T.SPEED], accel: row[T.ACCEL], brake: row[T.BRAKE],
        gear: row[T.GEAR], range: row[T.RANGE], rpm: row[T.RPM],
        soc: row[T.SOC], soh: row[T.SOH], dataHex: row[T.HEX], tx: row[T.TX],
      });
      const vehicleState = state.states.get(row[T.ID]);
      if (vehicleState) vehicleState.speed_kmh = row[T.SPEED];
    });

    // Hatta mesaj basmayan arac cevrimdisidir; bu, diger istemcilerin
    // verdigi komutlarin da bu panelde gorunmesini saglar.
    state.states.forEach((vehicleState, id) => {
      const online = transmitting.has(id);
      vehicleState.online = online;
      if (!online) state.telemetry.delete(id);
    });

    // Saniyede bir gelen tam durum senkronu (mod, hedef hiz, fren, cruise).
    if (message.states) {
      Object.entries(message.states).forEach(([id, vehicleState]) => {
        state.states.set(id, vehicleState);
        updateDriverStats(id, vehicleState);
      });
      const selected = selectedState();
      if (selected) {
        syncControlStates(selected);
        syncTargetInputs(selected.target_speed_kmh);
      }
    }

    if (message.frames) updateFrames(message.frames);
    if (!state.log.paused) writeLog(message);
    if (message.stats) updateStats(message.stats);

    el("monitor-updated").textContent = `#${message.seq} · ${timestamp()}`;
    state.dirty = true;
  }

  /** Secili aracin her PGN icin en son cercevesini panelde gunceller. */
  function updateFrames(frames) {
    frames.forEach((frame) => {
      if (frame.vehicle_id !== state.selectedId) return;
      updateFrameRow(frameRefs.get(frame.pgn), frame);
    });
  }

  /** Log satirina PGN'e ozgu kisa bir aciklama uretir. */
  function frameNote(frame) {
    const s = frame.signals || {};
    switch (frame.acronym) {
      case "CCVS1":
        return `${fmt(s.spn_84_wheel_based_speed_kmh)} km/h`;
      case "EEC2":
        return `gaz %${fmt(s.spn_91_accelerator_pedal_position_1_pct, 0)}`;
      case "ETC2":
        return `vites ${gearLabel(s.spn_523_current_gear, s.spn_163_current_range)}`;
      case "EBC1":
        return `fren %${fmt(s.spn_521_brake_pedal_position_pct, 0)}`;
      case "HVBATT":
        return `SOC %${fmt(s.spn_5464_state_of_charge_pct, 0)}`;
      default:
        return "";
    }
  }

  function writeLog(message) {
    const logEl = el("log");

    if (state.log.all) {
      if (message.seq % LOG_ALL_EVERY_N_TICKS !== 0) return;
      const time = timestamp();
      message.t.forEach((row) => {
        const vehicle = state.vehicles.get(row[T.ID]);
        if (!vehicle) return;
        appendLogRow(logEl, {
          time, acronym: "CCVS1", canId: vehicle.can_id_hex, data: row[T.HEX],
          note: `${vehicle.display_name} · ${fmt(row[T.SPEED])} km/h`,
        }, LOG_MAX_ROWS);
      });
      return;
    }

    (message.frames || []).forEach((frame) => {
      appendLogRow(logEl, {
        time: timestamp(),
        acronym: frame.acronym,
        canId: frame.can_id_hex,
        data: frame.data_hex,
        note: frameNote(frame),
      }, LOG_MAX_ROWS);
    });
  }

  function updateDriverStats(id, vehicleState) {
    let stats = state.driverStats.get(id);
    if (!stats) {
      stats = { harshBrakes: 0, overspeedCount: 0, prevBrake: false, wasOverspeed: false };
      state.driverStats.set(id, stats);
    }

    const speed = vehicleState.speed_kmh || 0;
    if (vehicleState.brake && !stats.prevBrake && speed > HARSH_BRAKE_SPEED_THRESHOLD) {
      stats.harshBrakes += 1;
    }
    stats.prevBrake = vehicleState.brake;

    const vehicle = state.vehicles.get(id);
    const limit = vehicle ? vehicle.max_speed_kmh * 0.95 : Infinity;
    const isOverspeed = vehicleState.online && speed > limit;
    if (isOverspeed && !stats.wasOverspeed) stats.overspeedCount += 1;
    stats.wasOverspeed = isOverspeed;

    stats.score = Math.min(100, Math.max(0, 100 - stats.harshBrakes * 8 - stats.overspeedCount * 4));
  }

  function updateStats(stats) {
    el("stat-online").textContent = `${stats.vehicles_online}/${stats.vehicles_total}`;
    el("stat-moving").textContent = String(stats.vehicles_moving);
    el("stat-fps").textContent = String(stats.bus_load_fps);
    el("stat-tx").textContent = stats.frames_sent.toLocaleString("tr-TR");
  }

  /* --------------------------------------------------------- secim/kontrol */

  function selectVehicle(vehicleId) {
    if (!state.vehicles.has(vehicleId)) return;
    state.selectedId = vehicleId;
    send({ type: "subscribe", vehicle_ids: [vehicleId] });
    el("log").textContent = "";
    frameRefs.forEach((node) => updateFrameRow(node, null));
    applySelection();
    state.dirty = true;
  }

  function applySelection() {
    const vehicle = selectedVehicle();
    const vehicleState = selectedState();
    if (!vehicle || !vehicleState) return;

    el("control-empty").hidden = true;
    el("control-form").hidden = false;

    el("sel-brand").textContent = vehicle.brand;
    el("sel-model").textContent = vehicle.model;
    el("sel-segment").textContent = vehicle.segment;
    el("sel-power").textContent = vehicle.power_hp;
    el("sel-sa").textContent = vehicle.source_address_hex;
    el("sel-powertrain").textContent = {
      diesel: "Dizel", hybrid: "Hibrit", electric: "Elektrikli",
    }[vehicle.powertrain] || vehicle.powertrain;
    el("selected-canid").textContent = vehicle.can_id_hex;

    // Secili aracin buyuk gorseli
    const art = el("sel-art");
    art.innerHTML = global.J1939VehicleArt(vehicle, { badge: false });
    global.J1939VehicleArt.bindFallbacks(art);

    // Gorsel bir kaynaktan alindiysa atif zorunlu olabilir (CC lisanslari).
    const credit = el("sel-art-credit");
    credit.textContent = vehicle.image_credit || "";
    credit.hidden = !vehicle.image_credit;

    // Vites secimi, modelin vites sayisina gore doldurulur.
    const gearSelect = el("gear-select");
    gearSelect.textContent = "";
    [["P", "P"], ["R", "R"], ["N", "N"], ["D", "D (otomatik)"]].forEach(([value, label]) => {
      gearSelect.appendChild(new Option(label, `range:${value}`));
    });
    for (let gear = 1; gear <= vehicle.gear_count; gear += 1) {
      gearSelect.appendChild(new Option(`${gear}. vites`, `gear:${gear}`));
    }

    syncTargetInputs(vehicleState.target_speed_kmh);
    syncControlStates(vehicleState);
  }

  function syncTargetInputs(targetSpeedKmh) {
    const active = document.activeElement;
    if (active === el("speed-slider") || active === el("speed-input")) return;

    el("speed-slider").value = String(Math.round(targetSpeedKmh));
    el("speed-input").value = fmt(targetSpeedKmh);
    document.querySelectorAll("[data-speed]").forEach((button) =>
      button.classList.toggle("is-active", Number(button.dataset.speed) === targetSpeedKmh)
    );
  }

  function syncControlStates(vehicleState) {
    el("tg-parking").classList.toggle("is-on", vehicleState.parking_brake);
    el("tg-cruise").classList.toggle("is-on", vehicleState.cruise_active);
    el("tg-online").classList.toggle("is-on", vehicleState.online);

    const active = document.activeElement;
    if (active !== el("accel-slider")) {
      el("accel-slider").value = String(Math.round(vehicleState.accel_pedal_pct));
      el("accel-value").textContent = `${fmt(vehicleState.accel_pedal_pct, 0)}%`;
    }
    if (active !== el("brake-slider")) {
      el("brake-slider").value = String(Math.round(vehicleState.brake_pedal_pct));
      el("brake-value").textContent = `${fmt(vehicleState.brake_pedal_pct, 0)}%`;
    }
    if (active !== el("soc-slider")) {
      el("soc-slider").value = String(Math.round(vehicleState.soc_pct));
    }
    if (active !== el("soh-slider")) {
      el("soh-slider").value = String(Math.round(vehicleState.soh_pct));
    }

    const gearSelect = el("gear-select");
    if (document.activeElement !== gearSelect) {
      gearSelect.value = vehicleState.gear_auto
        ? `range:${vehicleState.gear_range}`
        : `gear:${vehicleState.gear}`;
    }

    document.querySelectorAll("[data-mode]").forEach((button) =>
      button.classList.toggle("is-active", button.dataset.mode === vehicleState.mode)
    );
  }

  function injectSpeed(speedKmh, instant) {
    const vehicle = selectedVehicle();
    if (!vehicle) return;

    const max = state.meta.max_speed_kmh;
    const value = Math.min(Math.max(Number(speedKmh) || 0, 0), max);

    el("speed-slider").value = String(Math.round(value));
    el("speed-input").value = fmt(value);
    document.querySelectorAll("[data-speed]").forEach((button) =>
      button.classList.toggle("is-active", Number(button.dataset.speed) === value)
    );

    send({ type: "set_speed", vehicle_id: vehicle.id, speed_kmh: value, instant: !!instant });
  }

  /* ---------------------------------------------------------- render dongusu */

  function render() {
    if (state.dirty) {
      state.dirty = false;
      const max = state.meta.max_speed_kmh;

      state.states.forEach((vehicleState, id) => {
        const telemetry = state.telemetry.get(id);
        const isSelected = id === state.selectedId;
        updateCard(cardRefs.get(id), vehicleState, telemetry, max, isSelected);
        updateMonitorRow(monitorRefs.get(id), vehicleState, telemetry, max, isSelected);
      });

      const vehicle = selectedVehicle();
      const vehicleState = selectedState();
      if (vehicle && vehicleState) {
        const telemetry = state.telemetry.get(vehicle.id);
        const speed = vehicleState.online && telemetry ? telemetry.speed : 0;
        updateGauge(speed, vehicleState.target_speed_kmh, max);
        updateReadouts(vehicleState, telemetry);
        updateTelematicsReadouts(vehicleState);

        // Motor sesi secili aracin telemetrisini izler.
        audio.update({
          rpm: telemetry ? telemetry.rpm : 0,
          maxRpm: vehicle.max_rpm,
          load: vehicleState.engine_load_pct,
          speed,
          maxSpeed: max,
          powertrain: vehicle.powertrain,
          online: vehicleState.online,
        });

        if (state.activeTab === "location") {
          updateMap(vehicleState.latitude, vehicleState.longitude, vehicle.display_name);
        }

        const dtcCount = (vehicleState.active_dtcs || []).length;
        const badge = el("dtc-count");
        badge.hidden = dtcCount === 0;
        badge.textContent = String(dtcCount);
        if (state.activeTab === "dtc") {
          renderDtcList(el("dtc-list"), vehicleState.active_dtcs);
        }

        if (state.activeTab === "score") {
          const stats = state.driverStats.get(vehicle.id) || { score: 100, harshBrakes: 0, overspeedCount: 0 };
          updateScorePanel(stats.score, stats.harshBrakes, stats.overspeedCount);
          updateMaintenancePanel(vehicleState.odometer_km, SERVICE_INTERVAL_KM);
        }
      }
    }
    requestAnimationFrame(render);
  }

  /* ------------------------------------------------------------ olaylar */

  function bindEvents() {
    el("search").addEventListener("input", (event) => {
      state.filters.search = event.target.value;
      rebuildFleet();
    });

    el("brand-filter").addEventListener("change", (event) => {
      state.filters.brand = event.target.value;
      rebuildFleet();
    });

    document.querySelectorAll("[data-fleet-action]").forEach((button) =>
      button.addEventListener("click", () =>
        send({ type: "fleet_command", action: button.dataset.fleetAction })
      )
    );

    // --- hiz ---
    el("speed-slider").addEventListener("input", (event) => {
      el("speed-input").value = fmt(event.target.value);
    });
    el("speed-slider").addEventListener("change", (event) => injectSpeed(event.target.value));
    el("speed-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") injectSpeed(event.target.value);
    });
    el("inject-btn").addEventListener("click", () => injectSpeed(el("speed-input").value));
    el("inject-instant-btn").addEventListener("click", () => injectSpeed(el("speed-input").value, true));
    document.querySelectorAll("[data-speed]").forEach((button) =>
      button.addEventListener("click", () => injectSpeed(button.dataset.speed))
    );

    // --- gaz pedali (SPN 91) ---
    const sendAccel = (value) => {
      el("accel-value").textContent = `${fmt(value, 0)}%`;
      if (state.selectedId) {
        send({ type: "set_accelerator", vehicle_id: state.selectedId, pedal_pct: Number(value) });
      }
    };
    el("accel-slider").addEventListener("input", (e) => {
      el("accel-value").textContent = `${fmt(e.target.value, 0)}%`;
    });
    el("accel-slider").addEventListener("change", (e) => sendAccel(e.target.value));

    // --- fren pedali (SPN 521) ---
    el("brake-slider").addEventListener("input", (e) => {
      el("brake-value").textContent = `${fmt(e.target.value, 0)}%`;
    });
    el("brake-slider").addEventListener("change", (e) => {
      if (state.selectedId) {
        send({ type: "set_brake_pedal", vehicle_id: state.selectedId,
               pedal_pct: Number(e.target.value) });
      }
    });
    el("brake-stomp").addEventListener("click", () => {
      el("brake-slider").value = "100";
      el("brake-value").textContent = "100%";
      if (state.selectedId) {
        send({ type: "set_brake_pedal", vehicle_id: state.selectedId, pedal_pct: 100 });
      }
    });
    el("brake-release").addEventListener("click", () => {
      el("brake-slider").value = "0";
      el("brake-value").textContent = "0%";
      if (state.selectedId) {
        send({ type: "set_brake_pedal", vehicle_id: state.selectedId, pedal_pct: 0 });
      }
    });

    // --- vites (SPN 523 / 162-163) ---
    el("gear-select").addEventListener("change", (event) => {
      if (!state.selectedId) return;
      const [kind, value] = event.target.value.split(":");
      if (kind === "range") {
        send({ type: "set_gear_range", vehicle_id: state.selectedId, gear_range: value });
      } else {
        send({ type: "set_gear", vehicle_id: state.selectedId, gear: Number(value) });
      }
    });

    // --- batarya (SPN 5464 / 5465) ---
    el("soc-slider").addEventListener("change", (event) => {
      if (state.selectedId) {
        send({ type: "set_battery", vehicle_id: state.selectedId,
               soc_pct: Number(event.target.value) });
      }
    });
    el("soh-slider").addEventListener("change", (event) => {
      if (state.selectedId) {
        send({ type: "set_battery", vehicle_id: state.selectedId,
               soh_pct: Number(event.target.value) });
      }
    });

    // --- anahtarlar ---
    const toggle = (elementId, commandType, readState, extra) =>
      el(elementId).addEventListener("click", () => {
        const vehicleState = selectedState();
        if (!vehicleState) return;
        send({ type: commandType, vehicle_id: state.selectedId, ...extra(!readState(vehicleState)) });
      });

    toggle("tg-parking", "set_parking_brake", (s) => s.parking_brake, (v) => ({ value: v }));
    toggle("tg-online", "set_online", (s) => s.online, (v) => ({ value: v }));
    toggle("tg-cruise", "set_cruise", (s) => s.cruise_active, (v) => ({ active: v }));

    document.querySelectorAll("[data-mode]").forEach((button) =>
      button.addEventListener("click", () => {
        if (state.selectedId) {
          send({ type: "set_mode", vehicle_id: state.selectedId, mode: button.dataset.mode });
        }
      })
    );

    // --- motor sesi ---
    el("tg-audio").addEventListener("click", async () => {
      const button = el("tg-audio");
      if (audio.enabled) {
        audio.disable();
        button.classList.remove("is-on");
        button.querySelector(".toggle__text").textContent = "Motor Sesi Kapali";
      } else {
        // AudioContext yalnizca kullanici etkilesiminde baslatilabilir.
        const ok = await audio.enable();
        button.classList.toggle("is-on", ok);
        button.querySelector(".toggle__text").textContent =
          ok ? "Motor Sesi Acik" : "Ses desteklenmiyor";
      }
    });

    el("audio-volume").addEventListener("input", (event) => {
      audio.setVolume(Number(event.target.value) / 100);
    });

    // --- log ---
    el("log-pause").addEventListener("click", (event) => {
      state.log.paused = !state.log.paused;
      event.target.textContent = state.log.paused ? "Devam Et" : "Duraklat";
      event.target.classList.toggle("btn--primary", state.log.paused);
    });
    el("log-clear").addEventListener("click", () => { el("log").textContent = ""; });
    el("log-all").addEventListener("change", (event) => {
      state.log.all = event.target.checked;
      el("log").textContent = "";
    });

    /* -------------------------------------------------------------- sekmeler */

    document.querySelectorAll(".tab").forEach((button) =>
      button.addEventListener("click", () => {
        state.activeTab = button.dataset.tab;
        switchTab(state.activeTab);
        if (state.activeTab === "location") {
          requestAnimationFrame(() => invalidateMapSize());
        }
        state.dirty = true;
      })
    );

    /* ----------------------------------------------------------------- ariza */

    el("dtc-trigger").addEventListener("click", () => {
      if (state.selectedId) send({ type: "trigger_fault", vehicle_id: state.selectedId });
    });
    el("dtc-clear").addEventListener("click", () => {
      if (state.selectedId) send({ type: "clear_faults", vehicle_id: state.selectedId });
    });
    el("trip-reset").addEventListener("click", () => {
      if (state.selectedId) send({ type: "reset_trip", vehicle_id: state.selectedId });
    });

    /* --------------------------------------------------------------- lightbox */

    document.addEventListener("click", (event) => {
      const img = event.target.closest("img.vehicle-art--photo, .modal__preview");
      if (img && img.src) openLightbox(img.src, img.alt);
    });
    el("lightbox-close").addEventListener("click", closeLightbox);
    el("lightbox").addEventListener("click", (event) => {
      if (event.target.id === "lightbox") closeLightbox();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeLightbox();
    });

    /* ----------------------------------------------------------- arac ekle */

    bindAddVehicleModal();
  }

  function bindAddVehicleModal() {
    const overlay = el("add-vehicle-overlay");
    const form = el("add-vehicle-form");
    const errorEl = el("av-error");
    let imageDataUrl = null;

    const openModal = () => {
      form.reset();
      errorEl.hidden = true;
      imageDataUrl = null;
      el("av-image-preview").hidden = true;
      overlay.hidden = false;
    };
    const closeModal = () => { overlay.hidden = true; };

    el("open-add-vehicle").addEventListener("click", openModal);
    el("add-vehicle-close").addEventListener("click", closeModal);
    el("add-vehicle-cancel").addEventListener("click", closeModal);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });

    el("av-image").addEventListener("change", (event) => {
      const file = event.target.files[0];
      if (!file) { imageDataUrl = null; return; }
      const reader = new FileReader();
      reader.onload = () => {
        imageDataUrl = reader.result;
        const preview = el("av-image-preview");
        preview.src = imageDataUrl;
        preview.hidden = false;
      };
      reader.readAsDataURL(file);
    });

    const slug = (text) =>
      text.trim().toLocaleLowerCase("tr").replace(/ı/g, "i")
        .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "arac";

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      errorEl.hidden = true;

      const brand = el("av-brand").value.trim();
      const model = el("av-model").value.trim();
      if (!brand || !model) return;

      const body = {
        brand_id: slug(brand),
        brand,
        model_id: slug(model),
        model,
        segment: el("av-segment").value.trim() || "Ozel",
        country: el("av-country").value.trim() || "-",
        color: el("av-color").value || "#7d8590",
        power_hp: Number(el("av-power").value) || 0,
        max_speed_kmh: Number(el("av-maxspeed").value) || 90,
        powertrain: el("av-powertrain").value || "diesel",
        image_data_url: imageDataUrl,
      };

      try {
        await global.J1939Api.addVehicle(body);
        closeModal();
      } catch (error) {
        errorEl.textContent = error.message || "Arac eklenemedi.";
        errorEl.hidden = false;
      }
    });
  }

  /* ----------------------------------------------------------- baslangic */

  function bindSocket() {
    socket.onState((connectionState, detail) => {
      const node = el("conn");
      node.dataset.state = connectionState;
      node.querySelector(".conn__text").textContent = {
        open: "Bagli",
        connecting: detail ? `Yeniden baglaniyor · ${detail}` : "Baglaniyor…",
        closed: "Baglanti kesildi",
      }[connectionState];
    });

    socket.on("snapshot", applySnapshot);
    socket.on("telemetry", onTelemetry);

    socket.on("ack", (message) => {
      if (message.state && message.state.vehicle_id) {
        state.states.set(message.state.vehicle_id, message.state);
        if (message.state.vehicle_id === state.selectedId) {
          syncControlStates(message.state);
          syncTargetInputs(message.state.target_speed_kmh);
        }
        state.dirty = true;
      }
    });

    socket.on("error", (message) => console.error("Sunucu hatasi:", message.detail));
    socket.connect();
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindEvents();
    bindSocket();
    requestAnimationFrame(render);
  });

  global.J1939App = { state, socket, audio, selectVehicle, injectSpeed };
})(window);
