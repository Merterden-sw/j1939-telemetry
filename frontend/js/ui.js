/* =========================================================================
   DOM olusturma ve guncelleme yardimcilari.

   Kartlar ve tablo satirlari yalnizca bir kez olusturulur; telemetri
   akisinda sadece degisen hucre degerleri guncellenir (10 Hz'de 30 arac
   icin DOM yeniden olusturmak pahalidir).
   ========================================================================= */
(function (global) {
  "use strict";

  const GAUGE_ARC_LENGTH = 251.3; // yarim daire, r = 80

  const el = (id) => document.getElementById(id);
  const fmt = (value, digits = 1) => Number(value || 0).toFixed(digits);

  const MODE_LABEL = { manual: "MANUEL", auto: "OTO", idle: "ROLANTI" };

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
        return `${brand.name} ${vehicle.model} ${vehicle.segment}`
          .toLocaleLowerCase("tr")
          .includes(needle);
      });
      if (matching.length === 0) return;

      const group = document.createElement("div");
      group.className = "brand-group";

      const head = document.createElement("div");
      head.className = "brand-group__head";
      head.innerHTML = `
        <span class="brand-group__dot" style="background:${brand.color}"></span>
        <span class="brand-group__name">${brand.name}</span>
        <span class="brand-group__country">${brand.country}</span>`;
      group.appendChild(head);

      const cards = document.createElement("div");
      cards.className = "cards";

      matching.forEach((vehicle) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "card";
        card.dataset.vehicleId = vehicle.id;
        card.style.borderLeftColor = brand.color;
        card.innerHTML = `
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
          <span class="card__bar"><span style="width:0%"></span></span>`;
        card.addEventListener("click", () => onSelect(vehicle.id));
        cards.appendChild(card);

        refs.set(vehicle.id, {
          card,
          led: card.querySelector(".card__led"),
          speed: card.querySelector(".card__speed-value"),
          mode: card.querySelector(".card__mode"),
          bar: card.querySelector(".card__bar span"),
        });
      });

      group.appendChild(cards);
      container.appendChild(group);
    });

    if (refs.size === 0) {
      container.innerHTML = '<p class="empty">Aramayla eslesen arac yok.</p>';
    }
    return refs;
  }

  function updateCard(ref, vehicle, vehicleState, telemetry, maxSpeed, isSelected) {
    if (!ref) return;
    const speed = vehicleState.online ? (telemetry ? telemetry.speed : vehicleState.speed_kmh) : 0;

    ref.speed.textContent = fmt(speed);
    ref.mode.textContent = MODE_LABEL[vehicleState.mode] || vehicleState.mode;
    ref.bar.style.width = `${Math.min((speed / maxSpeed) * 100, 100)}%`;
    ref.led.dataset.state = !vehicleState.online ? "off" : speed > 0.5 ? "moving" : "idle";
    ref.card.classList.toggle("is-offline", !vehicleState.online);
    ref.card.classList.toggle("is-selected", isSelected);
  }

  /* ------------------------------------------------------ izleme tablosu */

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
        <td class="num raw-cell">0</td>
        <td class="mono-cell data-cell">--</td>
        <td class="num tx-cell">0</td>`;
      row.addEventListener("click", () => onSelect(vehicle.id));
      tbody.appendChild(row);

      refs.set(vehicle.id, {
        row,
        speed: row.querySelector(".speed-cell"),
        bar: row.querySelector(".mbar span"),
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
    const speed = online && telemetry ? telemetry.speed : 0;
    const ratio = Math.min(speed / maxSpeed, 1);

    ref.speed.textContent = fmt(speed);
    ref.bar.style.width = `${ratio * 100}%`;
    ref.bar.className = ratio > 0.9 ? "is-max" : ratio > 0.6 ? "is-fast" : "";
    ref.raw.textContent = online && telemetry ? Math.round(speed * 256) : "—";
    ref.data.textContent = online && telemetry ? telemetry.dataHex : "— sessiz —";
    ref.tx.textContent = telemetry ? telemetry.tx.toLocaleString("tr-TR") : "0";
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

  /* ------------------------------------------------------------- can log */

  function appendLogRow(logEl, entry, maxRows) {
    const row = document.createElement("div");
    row.className = "log__row";
    row.innerHTML = `
      <span class="log__time">${entry.time}</span>
      <span class="log__id">${entry.canId}</span>
      <span class="log__sep">#</span>
      <span class="log__data">${entry.data}</span>
      <span class="log__name">${entry.name}</span>
      <span class="log__speed">${fmt(entry.speed)} km/h</span>`;

    const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
    logEl.appendChild(row);
    while (logEl.childElementCount > maxRows) logEl.removeChild(logEl.firstElementChild);
    if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  }

  /* ------------------------------------------------------------ frame gorunumu */

  function updateFrameView(canIdHex, dataHex) {
    el("frame-current").textContent = `${canIdHex}#${dataHex}`;
    const bytes = el("frame-bytes");
    bytes.textContent = "";
    (dataHex.match(/../g) || []).forEach((byte, index) => {
      const cell = document.createElement("b");
      cell.textContent = byte;
      // Byte 2-3 (indeks 1-2) SPN 84 arac hizini tasir.
      if (index === 1 || index === 2) cell.className = "is-speed";
      cell.title = `Byte ${index + 1}`;
      bytes.appendChild(cell);
    });
  }

  global.J1939Ui = {
    el,
    fmt,
    renderFleet,
    updateCard,
    renderMonitor,
    updateMonitorRow,
    updateGauge,
    appendLogRow,
    updateFrameView,
  };
})(window);
