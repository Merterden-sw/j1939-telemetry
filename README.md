# J1939 Telemetri Kontrol Paneli

SAE J1939 standardina uygun **PGN 65265 (CCVS1)** mesajlarini ureten, 30 araclik
bir filoyu gercek zamanli simule eden ve WebSocket uzerinden canli izlenip
kontrol edilebilen Docker tabanli telemetri sistemi.

```
┌──────────────────┐   WS /ws (10 Hz)   ┌────────────────────────┐
│    frontend      │ ←────────────────→ │   backend_telemetry    │
│ nginx + vanilla  │   REST /api/*      │ FastAPI + simulator    │
│ JS dashboard     │ ←────────────────→ │ 30 arac × CCVS1 @100ms │
└──────────────────┘                    └────────────────────────┘
        :8080                                     :8000
```

## Hizli Baslangic

```bash
docker compose up --build
```

| Adres | Icerik |
|---|---|
| http://localhost:8080 | Kontrol paneli (arayuz) |
| http://localhost:8000/docs | Swagger / OpenAPI |
| http://localhost:8000/api/health | Servis saglik kontrolu |

Docker olmadan yerel calistirma:

```bash
cd backend && pip install -r requirements-dev.txt && uvicorn app.main:app --port 8000
```

```bash
cd frontend && python3 -m http.server 8081
```

Ardindan `http://localhost:8081/?api=http://localhost:8000` adresini acin
(`?api=` parametresi nginx proxy'si olmadan backend'i isaret etmeyi saglar).

---

## 1. J1939 Mesaj Yapisi

### 29-bit Genisletilmis Tanimlayici (J1939-21)

| Bit | Alan | Deger |
|---|---|---|
| 28–26 | Priority | `6` (varsayilan) |
| 25 | EDP | `0` |
| 24 | DP | `0` |
| 23–16 | PDU Format (PF) | `0xFE` |
| 15–8 | PDU Specific (PS) | `0xF1` (Group Extension) |
| 7–0 | Source Address (SA) | `0x00` … `0x1D` |

PGN 65265 = `0xFEF1`. PF = `0xFE ≥ 240` oldugundan mesaj **PDU2** (yayin)
tipindedir ve PS alani hedef adres degil, grup uzantisidir.

```
Priority 6 + PGN 0xFEF1 + SA 0x00  →  0x18FEF100
Priority 6 + PGN 0xFEF1 + SA 0x0A  →  0x18FEF10A
```

### CCVS1 Veri Alani (8 byte)

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| 1 | 69 / 70 / 1633 / 3807 | Iki kademeli aks, el freni, cruise pause | 2 bit/parametre |
| **2–3** | **84** | **Wheel-Based Vehicle Speed** | **1/256 km/h, little-endian** |
| 4 | 595 / 596 / 597 / 598 | Cruise aktif, cruise switch, **fren**, debriyaj | 2 bit/parametre |
| 5 | 599 / 600 / 601 / 602 | Cruise set / coast / resume / accel | 2 bit/parametre |
| 6 | 86 | Cruise Control Set Speed | 1 km/h |
| 7 | 976 / 527 | PTO durumu (5 bit) + cruise durumu (3 bit) | — |
| 8 | 968 / 967 / 966 / 1237 | Rolanti artir/azalt, test, kapatma gecersiz | 2 bit/parametre |

### SPN 84 Hex Donusum Mantigi

```
raw    = round(speed_kmh / (1/256)) = round(speed_kmh × 256)
byte2  = raw & 0xFF          (dusuk byte — once gonderilir)
byte3  = (raw >> 8) & 0xFF   (yuksek byte)
```

| Hiz | Ham deger | Byte 2 | Byte 3 | Tam cerceve (SA 0x00) |
|---|---|---|---|---|
| 0 km/h | `0x0000` | `00` | `00` | `18FEF100#F300000000FF1FFF` |
| 60 km/h | `0x3C00` | `00` | `3C` | `18FEF100#F3003C0000FF1FFF` |
| 87.5 km/h | `0x5780` | `80` | `57` | `18FEF100#F380570000FF1FFF` |
| 180 km/h | `0xB400` | `00` | `B4` | `18FEF100#F300B40000FF1FFF` |
| 250.996 km/h | `0xFAFF` | `FF` | `FA` | maksimum gecerli deger |
| veri yok | `0xFFFF` | `FF` | `FF` | "not available" isareti |

Aralik: **0 – 250.996 km/h**. `0xFE00` ve uzeri hata/veri-yok gostergeleridir,
bu nedenle gecerli maksimum ham deger `0xFAFF` (64255) olarak kirpilir.

Kod karsiligi: [`backend/app/j1939.py`](backend/app/j1939.py)

### Filo Veri Modeli

10 marka × 3 model = **30 arac**, her birine benzersiz bir J1939 kaynak adresi
(`0x00`–`0x1D`) atanmistir. Tanim: [`backend/data/vehicles.json`](backend/data/vehicles.json)

```json
{
  "id": "scania",
  "name": "Scania",
  "country": "Isvec",
  "color": "#ff453a",
  "models": [
    { "id": "s730", "name": "S 730", "segment": "Uzun Yol",
      "source_address": 10, "max_speed_kmh": 95, "power_hp": 730 }
  ]
}
```

Markalar: Mercedes-Benz, MAN, Volvo Trucks, Scania, DAF, Iveco, Renault Trucks,
Ford Trucks, BMC, Isuzu.

---

## 2. Backend / Simulator

| Modul | Sorumluluk |
|---|---|
| `app/j1939.py` | CAN ID kurulumu, SPN 84/86 kodlama-cozme, cerceve nesnesi (IO bagimsiz) |
| `app/fleet.py` | `vehicles.json` okuma, kaynak adres benzersizlik dogrulamasi |
| `app/simulator.py` | 100 ms periyotlu asenkron dongu, ivme modeli, komut isleme |
| `app/hub.py` | WebSocket yayini; istemci basina kuyruk, yavas istemci akisi bloke etmez |
| `app/main.py` | REST uclari, WebSocket komut kanali, yasam dongusu |

Simulasyon her tick'te her cevrimici arac icin bir CCVS1 cercevesi uretir
(30 arac × 10 Hz = **300 frame/s**). Cevrimdisi yapilan bir arac hatta hic mesaj
basmaz — gercek bir ECU'nun kapali olmasi gibi.

### REST Uclari

| Metot | Uc | Aciklama |
|---|---|---|
| `GET` | `/api/health` | Servis durumu |
| `GET` | `/api/meta` | J1939 mesaj tanimi ve sinirlar |
| `GET` | `/api/stats` | Frame sayaci, bus yuku, istemci sayisi |
| `GET` | `/api/vehicles` | Marka bazli gruplanmis filo + durumlar |
| `GET` | `/api/vehicles/{id}` | Tek arac tanimi ve durumu |
| `POST` | `/api/vehicles/{id}/speed` | **Hiz enjeksiyonu** `{"speed_kmh": 90, "instant": false}` |
| `POST` | `/api/vehicles/{id}/mode` | `manual` / `auto` / `idle` |
| `POST` | `/api/vehicles/{id}/online` | ECU'yu hatta al / hattan cikar |
| `POST` | `/api/vehicles/{id}/brake` | Fren anahtari (SPN 597) |
| `POST` | `/api/vehicles/{id}/parking-brake` | El freni (SPN 70) |
| `POST` | `/api/vehicles/{id}/cruise` | Cruise control (SPN 595 / 86) |
| `POST` | `/api/fleet/command` | `stop_all` / `auto_all` / `manual_all` / `resume_all` |
| `POST` | `/api/j1939/encode` | Durum degistirmeden cerceve uret |
| `POST` | `/api/j1939/decode` | `18FEF100#F3003C0000FF1FFF` cozumle |

```bash
# 90 km/h enjekte et
curl -X POST http://localhost:8000/api/vehicles/scania-s730/speed \
     -H 'Content-Type: application/json' -d '{"speed_kmh": 90}'

# Ham cerceveyi cozumle
curl -X POST http://localhost:8000/api/j1939/decode \
     -H 'Content-Type: application/json' -d '{"frame": "18FEF10A#F3005A0000FF1FFF"}'
```

### WebSocket Protokolu — `ws://localhost:8080/ws`

**Sunucu → istemci**

```jsonc
// baglanti aninda
{ "type": "snapshot", "brands": [...], "states": {...}, "meta": {...} }

// her 100 ms
{
  "type": "telemetry",
  "seq": 152,
  "ts": 1788335393.02,
  "t": [["scania-s730", 120.06, "F3014D0000FF1FFF", 5250], ...],  // tum filo, kompakt
  "frames": [ { "candump": "18FEF10A#...", "data_bytes": [...] } ], // abone olunanlar
  "stats": {...},      // saniyede bir
  "states": {...}      // saniyede bir - coklu istemci senkronu
}
```

Bant genisligi icin filo geneli `[id, hiz, veri_hex, tx]` dortlusu olarak
gonderilir; CAN ID araca sabit oldugundan istemci `candump` satirini kendisi
kurar. Tam cerceve detayi yalnizca abone olunan araclar icin iletilir.

**Istemci → sunucu**

```jsonc
{ "type": "subscribe",     "vehicle_ids": ["scania-s730"] }
{ "type": "set_speed",     "vehicle_id": "scania-s730", "speed_kmh": 120, "instant": false }
{ "type": "set_mode",      "vehicle_id": "scania-s730", "mode": "auto" }
{ "type": "set_brake",     "vehicle_id": "scania-s730", "value": true }
{ "type": "set_online",    "vehicle_id": "scania-s730", "value": false }
{ "type": "set_cruise",    "vehicle_id": "scania-s730", "active": true, "set_speed_kmh": 88 }
{ "type": "fleet_command", "action": "stop_all" }
{ "type": "ping" }
```

---

## 3. Web Arayuzu

Derleme adimi gerektirmeyen (vanilla JS) bir panel — nginx tarafindan statik
servis edilir, `/api` ve `/ws` istekleri backend'e proxy'lenir.

| Dosya | Icerik |
|---|---|
| `js/api.js` | REST istemcisi, uc nokta cozumleme |
| `js/socket.js` | Ussel geri cekilmeli yeniden baglanan WebSocket istemcisi |
| `js/ui.js` | DOM olusturma/guncelleme (kartlar ve satirlar bir kez kurulur) |
| `js/app.js` | Durum yonetimi, olay baglama, `requestAnimationFrame` render dongusu |

Bolumler:

- **Arac secim paneli** — marka bazli kart gridi, her kartta anlik hiz, mod ve
  baglanti LED'i; marka filtresi ve serbest metin aramasi.
- **Hiz enjektoru** — yarim daire gosterge, slider, nümerik girdi, hazir hiz
  butonlari, fren/el freni/cruise/ECU anahtarlari ve surus modu secimi.
- **CAN frame gorunumu** — uretilen `18FEF10A#F3014D0000FF1FFF` satiri ve
  SPN 84'un yerlestigi 2.–3. byte'lari vurgulayan byte dokumu.
- **CAN bus log** — zaman damgali canli cerceve akisi; duraklat, temizle ve
  "tum filo" secenekleri.
- **Canli izleme** — 30 aracin hizi, ham SPN 84 degeri, veri alani ve TX sayaci.

10 Hz'de 30 arac guncellenirken DOM yeniden olusturulmaz; yalnizca degisen
hucre degerleri yazilir ve boyama `requestAnimationFrame` ile birlestirilir.

---

## 4. Docker

| Servis | Imaj | Port | Aciklama |
|---|---|---|---|
| `backend_telemetry` | `python:3.12-slim` | 8000 | FastAPI + simulator, kok olmayan kullanici, healthcheck |
| `frontend` | `nginx:1.27-alpine` | 8080 → 80 | Statik arayuz + `/api` ve `/ws` proxy |

`frontend`, `backend_telemetry` saglikli olana kadar (`condition: service_healthy`)
beklenir. Backend imaji ayrica bir `test` katmani icerir:

```bash
docker build --target test -t j1939/backend:test ./backend && docker run --rm j1939/backend:test
```

## 5. CI/CD

[`.github/workflows/main.yml`](.github/workflows/main.yml) dort asamadan olusur:

1. **backend-test** — `ruff check` + `ruff format --check` + `pytest` (76 test)
2. **frontend-check** — JavaScript sozdizimi ve varlik dogrulamasi
3. **docker-build** — her iki imajin derlenmesi, testlerin konteyner icinde
   calistirilmasi, GitHub Actions katman onbellegi
4. **compose-smoke-test** — `docker compose up`, REST/WS uctan uca dogrulama
   (60 km/h → `18FEF100#F3003C0000FF1FFF` esitligi dahil)

> Workflow dosyasi **depo kokunde** `.github/workflows/` altinda bulunmalidir.
> Proje bir alt dizinde duruyorsa dosyanin basindaki `PROJECT_DIR` degerini
> guncelleyin (ornek: `PROJECT_DIR: j1939-telemetry`).

## 6. Testler

```bash
cd backend && python -m pytest -v
```

| Dosya | Kapsam |
|---|---|
| `tests/test_j1939.py` | CAN ID kurulumu, SPN 84 cozunurlugu/byte sirasi/kirpma, bit paketleme |
| `tests/test_simulator.py` | Hiz rampasi, fren, cruise, cevrimdisi davranis, filo komutlari |
| `tests/test_api.py` | REST uclari, dogrulama hatalari, WebSocket akisi ve komutlari |

## 7. Yapilandirma

Tum ayarlar ortam degiskeni ile gecersiz kilinabilir — bkz. [`.env.example`](.env.example)
ve `docker-compose.yml`.

| Degisken | Varsayilan | Aciklama |
|---|---|---|
| `SIM_TICK_MS` | `100` | CCVS1 yayin periyodu |
| `MAX_SPEED_KMH` | `180` | Enjekte edilebilir ust sinir |
| `SIM_ACCEL_KMH_S` | `5` | Ivmelenme |
| `SIM_DECEL_KMH_S` | `7` | Yavaslama |
| `SIM_BRAKE_DECEL_KMH_S` | `14` | Frenle yavaslama |
| `SIM_SPEED_NOISE_KMH` | `0.15` | Tekerlek sensoru gurultusu |
| `CORS_ORIGINS` | `*` | Izinli kaynaklar |

## Proje Yapisi

```
j1939-telemetry/
├── backend/
│   ├── app/
│   │   ├── j1939.py          # PGN 65265 / SPN 84 kodlama-cozme
│   │   ├── fleet.py          # filo tanimi ve dogrulama
│   │   ├── simulator.py      # 100 ms simulasyon dongusu
│   │   ├── hub.py            # WebSocket yayin katmani
│   │   ├── models.py         # Pydantic semalari
│   │   ├── config.py         # ortam degiskeni ayarlari
│   │   └── main.py           # FastAPI uygulamasi
│   ├── data/vehicles.json    # 10 marka × 3 model
│   ├── tests/                # 76 test
│   └── Dockerfile            # base + test katmanlari
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   ├── js/{api,socket,ui,app}.js
│   ├── nginx.conf            # statik servis + /api ve /ws proxy
│   └── Dockerfile
├── .github/workflows/main.yml
├── docker-compose.yml
├── Makefile
└── .env.example
```
