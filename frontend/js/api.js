/* =========================================================================
   REST istemcisi ve uc nokta cozumleme.

   Varsayilan olarak ayni origin kullanilir (nginx /api ve /ws isteklerini
   backend'e proxy'ler). Gelistirme sirasinda sayfayi dosyadan acarken veya
   farkli bir backend'e baglanirken ?api=http://localhost:8000 kullanilabilir.
   ========================================================================= */
(function (global) {
  "use strict";

  const params = new URLSearchParams(global.location.search);

  function resolveHttpBase() {
    const override = params.get("api") || global.__J1939_API__;
    if (override) return override.replace(/\/+$/, "");
    if (global.location.protocol === "file:") return "http://localhost:8000";
    return ""; // ayni origin
  }

  function resolveWsUrl() {
    const override = params.get("ws");
    if (override) return override;

    const httpBase = resolveHttpBase();
    if (httpBase) return httpBase.replace(/^http/, "ws") + "/ws";

    const scheme = global.location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${global.location.host}/ws`;
  }

  const HTTP_BASE = resolveHttpBase();
  const WS_URL = resolveWsUrl();

  async function request(path, options) {
    const response = await fetch(HTTP_BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        detail = (await response.json()).detail || detail;
      } catch (_) { /* govde JSON degil */ }
      throw new Error(`${response.status} ${detail}`);
    }
    return response.json();
  }

  const post = (path, body) =>
    request(path, { method: "POST", body: JSON.stringify(body || {}) });

  global.J1939Api = {
    HTTP_BASE,
    WS_URL,
    endpointLabel: (HTTP_BASE || global.location.origin) + " → " + WS_URL,

    health:      () => request("/api/health"),
    meta:        () => request("/api/meta"),
    stats:       () => request("/api/stats"),
    vehicles:    () => request("/api/vehicles"),
    vehicle:     (id) => request(`/api/vehicles/${encodeURIComponent(id)}`),

    setSpeed:    (id, speedKmh, instant) => post(`/api/vehicles/${id}/speed`, { speed_kmh: speedKmh, instant: !!instant }),
    setMode:     (id, mode) => post(`/api/vehicles/${id}/mode`, { mode }),
    setOnline:   (id, value) => post(`/api/vehicles/${id}/online`, { value }),
    setBrake:    (id, value) => post(`/api/vehicles/${id}/brake`, { value }),
    setParking:  (id, value) => post(`/api/vehicles/${id}/parking-brake`, { value }),
    setCruise:   (id, active, setSpeedKmh) => post(`/api/vehicles/${id}/cruise`, { active, set_speed_kmh: setSpeedKmh ?? null }),
    fleetCommand:(action) => post("/api/fleet/command", { action }),

    encode:      (speedKmh, sourceAddress) => post("/api/j1939/encode", { speed_kmh: speedKmh, source_address: sourceAddress }),
    decode:      (frame) => post("/api/j1939/decode", { frame }),
  };
})(window);
