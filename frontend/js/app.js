/* =========================================================================
   Uygulama cekirdegi: durum yonetimi, WebSocket akisi ve olay baglama.
   ========================================================================= */
(function (global) {
  "use strict";

  const { el, fmt, renderFleet, updateCard, renderMonitor, updateMonitorRow,
          updateGauge, appendLogRow, updateFrameView } = global.J1939Ui;

  const LOG_MAX_ROWS = 300;
  const LOG_ALL_EVERY_N_TICKS = 10; // tum filo modunda saniyede ~1 tur

  const state = {
    brands: [],
    vehicles: new Map(),   // id -> arac tanimi
    states: new Map(),     // id -> simulator durumu
    telemetry: new Map(),  // id -> { speed, dataHex, tx }
    meta: { max_speed_kmh: 180, tick_ms: 100 },
    selectedId: null,
    filters: { search: "", brand: "" },
    log: { paused: false, all: false },
    dirty: false,
  };

  const cardRefs = new Map();
  let monitorRefs = new Map();
  const socket = new global.J1939Socket(global.J1939Api.WS_URL);

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

    state.vehicles.clear();
    message.brands.forEach((brand) =>
      brand.vehicles.forEach((vehicle) => state.vehicles.set(vehicle.id, vehicle))
    );
    Object.entries(message.states).forEach(([id, vehicleState]) =>
      state.states.set(id, vehicleState)
    );

    el("fleet-count").textContent = `${state.vehicles.size} arac`;
    el("footer-endpoint").textContent = global.J1939Api.endpointLabel;

    const filter = el("brand-filter");
    if (filter.childElementCount <= 1) {
      message.brands.forEach((brand) => {
        const option = document.createElement("option");
        option.value = brand.id;
        option.textContent = brand.name;
        filter.appendChild(option);
      });
    }

    const slider = el("speed-slider");
    slider.max = String(state.meta.max_speed_kmh);
    el("speed-input").max = String(state.meta.max_speed_kmh);

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

    message.t.forEach(([id, speed, dataHex, tx]) => {
      transmitting.add(id);
      state.telemetry.set(id, { speed, dataHex, tx });
      const vehicleState = state.states.get(id);
      if (vehicleState) vehicleState.speed_kmh = speed;
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
      Object.entries(message.states).forEach(([id, vehicleState]) =>
        state.states.set(id, vehicleState)
      );
      const selected = selectedState();
      if (selected) {
        syncControlStates(selected);
        syncTargetInputs(selected.target_speed_kmh);
      }
    }

    if (!state.log.paused) writeLog(message);
    if (message.stats) updateStats(message.stats);

    el("monitor-updated").textContent = `#${message.seq} · ${timestamp()}`;
    state.dirty = true;
  }

  function writeLog(message) {
    const logEl = el("log");

    if (state.log.all) {
      if (message.seq % LOG_ALL_EVERY_N_TICKS !== 0) return;
      const time = timestamp();
      message.t.forEach(([id, speed, dataHex]) => {
        const vehicle = state.vehicles.get(id);
        if (!vehicle) return;
        appendLogRow(logEl, {
          time, canId: vehicle.can_id_hex, data: dataHex,
          name: vehicle.display_name, speed,
        }, LOG_MAX_ROWS);
      });
      return;
    }

    const frames = message.frames || [];
    frames.forEach((frame) => {
      appendLogRow(logEl, {
        time: timestamp(),
        canId: frame.can_id_hex,
        data: frame.data_hex,
        name: frame.display_name,
        speed: frame.speed_kmh,
      }, LOG_MAX_ROWS);
    });
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
    el("selected-canid").textContent = vehicle.can_id_hex;

    syncTargetInputs(vehicleState.target_speed_kmh);
    syncControlStates(vehicleState);
  }

  function syncTargetInputs(targetSpeedKmh) {
    // Kullanici o an bir alani duzenliyorsa uzerine yazma.
    const active = document.activeElement;
    if (active === el("speed-slider") || active === el("speed-input")) return;

    el("speed-slider").value = String(Math.round(targetSpeedKmh));
    el("speed-input").value = fmt(targetSpeedKmh);
    document.querySelectorAll("[data-speed]").forEach((button) =>
      button.classList.toggle("is-active", Number(button.dataset.speed) === targetSpeedKmh)
    );
  }

  function syncControlStates(vehicleState) {
    el("tg-brake").classList.toggle("is-on", vehicleState.brake);
    el("tg-parking").classList.toggle("is-on", vehicleState.parking_brake);
    el("tg-cruise").classList.toggle("is-on", vehicleState.cruise_active);
    el("tg-online").classList.toggle("is-on", vehicleState.online);

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
        updateCard(cardRefs.get(id), state.vehicles.get(id), vehicleState, telemetry, max, isSelected);
        updateMonitorRow(monitorRefs.get(id), vehicleState, telemetry, max, isSelected);
      });

      const vehicle = selectedVehicle();
      const vehicleState = selectedState();
      if (vehicle && vehicleState) {
        const telemetry = state.telemetry.get(vehicle.id);
        const speed = vehicleState.online && telemetry ? telemetry.speed : 0;
        updateGauge(speed, vehicleState.target_speed_kmh, max);
        updateFrameView(vehicle.can_id_hex, telemetry ? telemetry.dataHex : "----------------");
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

    document.querySelectorAll("[data-mode]").forEach((button) =>
      button.addEventListener("click", () => {
        if (state.selectedId) send({ type: "set_mode", vehicle_id: state.selectedId, mode: button.dataset.mode });
      })
    );

    const toggle = (elementId, commandType, readState, extra) =>
      el(elementId).addEventListener("click", () => {
        const vehicleState = selectedState();
        if (!vehicleState) return;
        send({ type: commandType, vehicle_id: state.selectedId, ...extra(!readState(vehicleState)) });
      });

    toggle("tg-brake",   "set_brake",         (s) => s.brake,          (v) => ({ value: v }));
    toggle("tg-parking", "set_parking_brake", (s) => s.parking_brake,  (v) => ({ value: v }));
    toggle("tg-online",  "set_online",        (s) => s.online,         (v) => ({ value: v }));
    toggle("tg-cruise",  "set_cruise",        (s) => s.cruise_active,  (v) => ({ active: v }));

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

  global.J1939App = { state, socket, selectVehicle, injectSpeed };
})(window);
