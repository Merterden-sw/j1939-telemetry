/* =========================================================================
   Arac gorselleri.

   Iki kaynak desteklenir:

     1. Gercek gorsel - aracin vehicles.json tanimindaki "image" alani doluysa
        (ornek: "img/mercedes-benz-actros.jpg") o dosya gosterilir.
     2. Yerlesik SVG cizimi - "image" bos ise ya da dosya yuklenemezse aracin
        segmentine gore yerinde cizilen siluet kullanilir.

   Varsayilan SVG'dir; boylece proje hicbir dis kaynaga bagimli olmadan,
   internet olmadan ve siki bir CSP altinda da calisir. Gorsel eklemek icin
   frontend/img/README.md dosyasina bakin.
   ========================================================================= */
(function (global) {
  "use strict";

  // Segment -> govde tipi eslesmesi
  const BODY_BY_SEGMENT = {
    "Uzun Yol": "tractor",
    "Agir Ticari": "tractor",
    "Insaat": "tipper",
    "Arazi": "tipper",
    "Bolgesel": "box",
    "Dagitim": "box",
    "Hafif": "van",
  };

  const VIEWBOX = "0 0 320 132";

  /* ------------------------------------------------------------ yardimcilar */

  function wheel(cx, cy, r) {
    return `
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="#12161c" />
      <circle cx="${cx}" cy="${cy}" r="${r * 0.55}" fill="#39424f" />
      <circle cx="${cx}" cy="${cy}" r="${r * 0.22}" fill="#6b7684" />`;
  }

  function shadow() {
    return `<ellipse cx="160" cy="120" rx="140" ry="6" fill="rgba(0,0,0,.35)" />`;
  }

  /** Kabin camini olusturan egimli dortgen. */
  function windshield(x, y, w, h, skew) {
    return `<path d="M${x + skew} ${y} H${x + w} V${y + h} H${x} Z"
             fill="rgba(140,190,240,.45)" stroke="rgba(255,255,255,.15)" />`;
  }

  /* ------------------------------------------------------------ govde tipleri */

  function tractor(color) {
    return `
      ${shadow()}
      <!-- dorse -->
      <rect x="118" y="26" width="184" height="66" rx="4" fill="#e8edf3" />
      <rect x="118" y="26" width="184" height="10" rx="3" fill="#cfd8e3" />
      <rect x="130" y="44" width="160" height="34" rx="2" fill="#f6f8fb" />
      <rect x="118" y="92" width="184" height="8" fill="#39424f" />
      <!-- cekici sasi -->
      <rect x="24" y="84" width="104" height="12" rx="3" fill="#39424f" />
      <!-- kabin -->
      <path d="M26 24 H108 a6 6 0 0 1 6 6 V88 H26 a6 6 0 0 1 -6 -6 V30 a6 6 0 0 1 6 -6 Z"
            fill="${color}" />
      ${windshield(30, 32, 62, 26, 10)}
      <rect x="22" y="70" width="92" height="8" fill="rgba(0,0,0,.22)" />
      <rect x="20" y="52" width="6" height="14" rx="2" fill="#f0c419" />
      <!-- egzoz -->
      <rect x="112" y="18" width="7" height="66" rx="3" fill="#9aa5b1" />
      ${wheel(52, 100, 17)}
      ${wheel(104, 100, 17)}
      ${wheel(238, 100, 17)}
      ${wheel(282, 100, 17)}`;
  }

  function tipper(color) {
    return `
      ${shadow()}
      <!-- damper -->
      <path d="M116 34 H296 l-8 58 H124 Z" fill="#d8a13a" />
      <path d="M124 44 H286 l-5 40 H129 Z" fill="#c08f2f" />
      <rect x="112" y="88" width="188" height="10" fill="#39424f" />
      <!-- sasi -->
      <rect x="24" y="84" width="98" height="14" rx="3" fill="#39424f" />
      <!-- kabin -->
      <path d="M28 22 H104 a6 6 0 0 1 6 6 V88 H28 a6 6 0 0 1 -6 -6 V28 a6 6 0 0 1 6 -6 Z"
            fill="${color}" />
      ${windshield(32, 30, 58, 26, 9)}
      <rect x="24" y="68" width="88" height="10" fill="rgba(0,0,0,.22)" />
      <rect x="20" y="76" width="96" height="7" rx="3" fill="#2b323c" />
      ${wheel(56, 100, 19)}
      ${wheel(112, 100, 19)}
      ${wheel(232, 100, 19)}
      ${wheel(280, 100, 19)}`;
  }

  function box(color) {
    return `
      ${shadow()}
      <!-- kasa -->
      <rect x="96" y="30" width="184" height="64" rx="4" fill="#eef2f7" />
      <rect x="106" y="42" width="164" height="40" rx="2" fill="#f8fafc" />
      <rect x="96" y="30" width="184" height="9" rx="3" fill="#d5dee8" />
      <line x1="188" y1="42" x2="188" y2="82" stroke="#cfd8e3" stroke-width="2" />
      <!-- kabin -->
      <path d="M28 34 H92 a5 5 0 0 1 5 5 V94 H28 a6 6 0 0 1 -6 -6 V40 a6 6 0 0 1 6 -6 Z"
            fill="${color}" />
      ${windshield(32, 40, 50, 24, 8)}
      <rect x="24" y="74" width="72" height="8" fill="rgba(0,0,0,.2)" />
      <rect x="22" y="86" width="258" height="8" fill="#39424f" />
      ${wheel(58, 100, 16)}
      ${wheel(238, 100, 16)}`;
  }

  function van(color) {
    return `
      ${shadow()}
      <path d="M44 40 H176 l30 16 H250 a8 8 0 0 1 8 8 V94 H44 a6 6 0 0 1 -6 -6 V46 a6 6 0 0 1 6 -6 Z"
            fill="${color}" />
      <rect x="60" y="50" width="108" height="26" rx="3" fill="#eef2f7" />
      ${windshield(178, 50, 48, 22, 12)}
      <rect x="38" y="82" width="220" height="8" fill="rgba(0,0,0,.22)" />
      ${wheel(84, 96, 15)}
      ${wheel(220, 96, 15)}`;
  }

  const BODIES = { tractor, tipper, box, van };

  /* ------------------------------------------------------------ rozet */

  const POWERTRAIN = {
    electric: { fill: "#30d158", label: "EV" },
    hybrid: { fill: "#0a84ff", label: "HEV" },
    diesel: { fill: "#7d8590", label: "D" },
  };

  const powertrainStyle = (p) => POWERTRAIN[p] || POWERTRAIN.diesel;

  /** SVG icine gomulen rozet. */
  function powertrainBadge(powertrain) {
    const style = powertrainStyle(powertrain);
    return `
      <g transform="translate(268, 8)">
        <rect width="44" height="18" rx="9" fill="${style.fill}" opacity=".92" />
        <text x="22" y="13" text-anchor="middle" font-size="10.5" font-weight="700"
              fill="#0d1117" font-family="system-ui, sans-serif">${style.label}</text>
      </g>`;
  }

  /** Fotograf uzerine bindirilen HTML rozeti. */
  function badgeElement(powertrain) {
    const style = powertrainStyle(powertrain);
    return `<span class="art-badge" style="background:${style.fill}">${style.label}</span>`;
  }

  const escapeAttr = (value) =>
    String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;");

  /* ------------------------------------------------------------ genel API */

  /** Aracin segmentine gore yerinde cizilen SVG silueti. */
  function drawing(vehicle, options) {
    const opts = options || {};
    const body = BODY_BY_SEGMENT[vehicle.segment] || "box";
    const draw = BODIES[body] || BODIES.box;
    const badge = opts.badge === false ? "" : powertrainBadge(vehicle.powertrain);

    return `<svg class="vehicle-art" viewBox="${VIEWBOX}" role="img"
      aria-label="${escapeAttr(vehicle.display_name)} temsili gorsel"
      preserveAspectRatio="xMidYMid meet">
      ${draw(vehicle.color)}
      ${badge}
    </svg>`;
  }

  /**
   * Gercek gorsel etiketi. Yuklenemezse bindFallbacks() bunu SVG ile degistirir;
   * bu yuzden geri donus icin gereken veriler data-* alanlarinda tasinir.
   */
  function photo(vehicle, options) {
    const opts = options || {};
    const withBadge = opts.badge !== false;
    return `<img class="vehicle-art vehicle-art--photo"
      src="${escapeAttr(vehicle.image)}"
      alt="${escapeAttr(vehicle.display_name)}"
      loading="lazy" decoding="async"
      data-segment="${escapeAttr(vehicle.segment)}"
      data-color="${escapeAttr(vehicle.color)}"
      data-powertrain="${escapeAttr(vehicle.powertrain)}"
      data-name="${escapeAttr(vehicle.display_name)}"
      data-badge="${withBadge}" />${withBadge ? badgeElement(vehicle.powertrain) : ""}`;
  }

  /**
   * Arac gorselini uretir: "image" alani doluysa fotograf, degilse SVG cizim.
   * @param {object} vehicle - /api/vehicles ciktisindaki arac tanimi
   * @param {object} [options] - { badge: bool }
   */
  function vehicleArt(vehicle, options) {
    return vehicle && vehicle.image ? photo(vehicle, options) : drawing(vehicle, options);
  }

  /**
   * Yuklenemeyen fotograflari sessizce SVG cizime dondurur.
   * Satir ici onerror yerine dinleyici kullanilir; boylece siki bir CSP
   * altinda da calisir. Kart/panel her yeniden olusturuldugunda cagrilir.
   */
  function bindFallbacks(root) {
    (root || document).querySelectorAll("img.vehicle-art--photo").forEach((img) => {
      if (img.dataset.fallbackBound) return;
      img.dataset.fallbackBound = "1";
      img.addEventListener("error", () => {
        const vehicle = {
          segment: img.dataset.segment,
          color: img.dataset.color,
          powertrain: img.dataset.powertrain,
          display_name: img.dataset.name,
        };
        const withBadge = img.dataset.badge !== "false";
        // Fotografa ait HTML rozeti kaldirilir; SVG kendi rozetini tasir.
        const badge = img.parentElement && img.parentElement.querySelector(".art-badge");
        if (badge) badge.remove();
        img.insertAdjacentHTML("afterend", drawing(vehicle, { badge: withBadge }));
        img.remove();
      }, { once: true });
    });
  }

  vehicleArt.bodyType = (vehicle) => BODY_BY_SEGMENT[vehicle.segment] || "box";
  vehicleArt.drawing = drawing;
  vehicleArt.bindFallbacks = bindFallbacks;

  global.J1939VehicleArt = vehicleArt;
})(window);
