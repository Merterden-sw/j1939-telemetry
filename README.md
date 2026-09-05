# J1939 Telemetri Kontrol Paneli

SAE J1939 standardina uygun dokuz farkli mesaji (hiz, gaz pedali, vites, fren
pedali, batarya, ariza kodlari, konum, motor saati, mesafe) gercek zamanli
ureten, 30 araclik bir filoyu simule eden ve WebSocket uzerinden canli izlenip
kontrol edilebilen Docker tabanli bir Fleet Telematics platformu. Arayuzde her
arac icin gercek/gorsel kart, canli harita, ariza paneli, surus skoru,
dijital gostergeler ve **Web Audio API ile sentezlenen dinamik motor sesi**
bulunur.

```
┌──────────────────┐   WS /ws (10 Hz)   ┌────────────────────────┐
│    frontend      │ ←────────────────→ │   backend_telemetry    │
│ nginx + vanilla  │   REST /api/*      │ FastAPI + simulator    │
│ JS + Leaflet     │ ←────────────────→ │ 30 arac × 9 PGN        │
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

Ardindan `http://localhost:8081/?api=http://localhost:8000` adresini acin.

---

## 1. Desteklenen J1939 Mesajlari

| PGN | Hex | Kisaltma | Mesaj | Oncelik | Periyot |
|---|---|---|---|---|---|
| 65265 | `0xFEF1` | CCVS1 | Cruise Control / Vehicle Speed 1 | 6 | 100 ms |
| 61443 | `0xF003` | EEC2 | Electronic Engine Controller 2 | 3 | 50 ms |
| 61445 | `0xF005` | ETC2 | Electronic Transmission Controller 2 | 3 | 100 ms |
| 61441 | `0xF001` | EBC1 | Electronic Brake Controller 1 | 6 | 100 ms |
| 64923 | `0xFD9B` | HVBATT | Yuksek gerilim batarya paketi | 6 | 1000 ms |
| 65226 | `0xFECA` | DM1 | Active Diagnostic Trouble Codes (basitlestirilmis) | 6 | 1000 ms |
| 65267 | `0xFEF3` | VEP1 | Vehicle Position (Latitude/Longitude) | 6 | 1000 ms |
| 65253 | `0xFEE5` | HOURS | Engine Hours, Revolutions | 6 | 5000 ms |
| 65248 | `0xFEE0` | VDHR | High Resolution Vehicle Distance | 6 | 1000 ms |

Tumu **PDU2** (yayin) tipindedir: PF ≥ 240 oldugu icin PS alani hedef adres
degil, grup uzantisidir. DM1 gercek J1939-73'te coklu-cerceve (BAM) tasinir;
bu simulator basitlestirme yapip tek DTC'yi sabit 8 byte'a sigdirir, tam
ariza listesi WS/REST JSON govdesinde (`active_dtcs`) ayrica tasinir.

### 29-bit Genisletilmis Tanimlayici (J1939-21)

| Bit | Alan |
|---|---|
| 28–26 | Priority |
| 25 | EDP (Extended Data Page) |
| 24 | DP (Data Page) |
| 23–16 | PF (PDU Format) |
| 15–8 | PS (PDU Specific / Group Extension) |
| 7–0 | SA (Source Address) |

```
Priority 6 + PGN 0xFEF1 + SA 0x00  →  0x18FEF100   (CCVS1)
Priority 3 + PGN 0xF003 + SA 0x00  →  0x0CF00300   (EEC2)
Priority 3 + PGN 0xF005 + SA 0x0A  →  0x0CF0050A   (ETC2)
Priority 6 + PGN 0xF001 + SA 0x00  →  0x18F00100   (EBC1)
Priority 6 + PGN 0xFD9B + SA 0x07  →  0x18FD9B07   (HVBATT)
```

### SPN yerlesimleri

**CCVS1 (65265)**

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| 1 | 69 / **70** / 1633 / 3807 | iki kademeli aks, **el freni**, cruise pause | 2 bit |
| **2–3** | **84** | **Wheel-Based Vehicle Speed** | **1/256 km/h, little-endian** |
| 4 | 595 / 596 / **597** / 598 | cruise aktif, cruise switch, **fren anahtari**, debriyaj | 2 bit |
| 5 | 599–602 | cruise set / coast / resume / accel | 2 bit |
| 6 | 86 | Cruise Control Set Speed | 1 km/h |
| 7 | 976 / 527 | PTO durumu (5 bit) + cruise durumu (3 bit) | — |
| 8 | 968 / 967 / 966 / 1237 | rolanti artir/azalt, test, kapatma | 2 bit |

**EEC2 (61443)**

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| 1 | 558 / 559 / 1437 / 2970 | rolanti anahtari, kickdown | 2 bit |
| **2** | **91** | **Accelerator Pedal Position 1** | **0.4 %/bit, 0–100 %** |
| 3 | 92 | Engine Percent Load At Current Speed | 1 %/bit |
| 4 | 974 | Remote Accelerator Pedal Position | 0.4 %/bit |
| 5 | 29 | Accelerator Pedal Position 2 | 0.4 %/bit |
| 6–8 | — | kullanilmiyor (`0xFF`) | — |

**ETC2 (61445)**

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| 1 | 524 | Transmission Selected Gear | 1 vites/bit, offset **−125** |
| 2–3 | 526 | Transmission Actual Gear Ratio | 0.001/bit, little-endian |
| **4** | **523** | **Transmission Current Gear** | **1 vites/bit, offset −125** |
| 5–6 | 162 | Transmission Requested Range | 2 ASCII karakter |
| 7–8 | 163 | Transmission Current Range | 2 ASCII karakter |

**EBC1 (61441)**

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| 1 | 561 / 562 / 563 / 1121 | ASR, **ABS**, EBS fren anahtari | 2 bit |
| **2** | **521** | **Brake Pedal Position** | **0.4 %/bit, 0–100 %** |
| 3 | 575–577 / 1238 | ABS/ASR offroad, hill holder | 2 bit |
| 4–8 | — | kullanilmiyor (`0xFF`) | — |

**HVBATT (64923)**

| Byte | SPN | Parametre | Cozunurluk |
|---|---|---|---|
| **1** | **5464** | **State of Charge (SOC)** | **0.4 %/bit, 0–100 %** |
| **2** | **5465** | **State of Health (SOH)** | **0.4 %/bit, 0–100 %** |
| 3–8 | — | kullanilmiyor (`0xFF`) | — |

> Bu PGN'in 3–8. byte yerlesimi standarttan dogrulanamadigi icin yalnizca
> tanimli iki SPN yayinlanir; kalan byte'lar "veri yok" olarak birakilir.

### Donusum mantigi

```
# Olculen degerler
raw    = round((deger - offset) / cozunurluk)

# SPN 84 (2 byte, little-endian)
raw    = round(km/h * 256)
byte2  = raw & 0xFF          byte3 = (raw >> 8) & 0xFF

# SPN 91 / 521 / 5464 / 5465 (1 byte)
raw    = round(yuzde / 0.4)          # %100 -> 250

# SPN 523 / 524 (1 byte, offset -125)
raw    = vites + 125                 # bos -> 125, 1. vites -> 126, geri -> 124
```

| Sinyal | Deger | Ham | Cerceve (SA 0x00) |
|---|---|---|---|
| SPN 84 | 60 km/h | `0x3C00` | `18FEF100#F3003C0000FF1FFF` |
| SPN 84 | 180 km/h | `0xB400` | `18FEF100#F300B40000FF1FFF` |
| SPN 91 | %50 | `0x7D` | `0CF00300#FC7DFFFFFFFFFFFF` |
| SPN 523 | 8. vites, D | `0x85` | `0CF00500#85FFFF8544204420` |
| SPN 5464/5465 | %78 / %96 | `0xC3` / `0xF0` | `18FD9B00#C3F0FFFFFFFFFFFF` |

Ayrilmis degerler: `0xFF` / `0xFFFF` = veri yok, `0xFE` / `0xFE00` = hata.
SPN 84 icin gecerli maksimum `0xFAFF` (250.996 km/h).

Kod karsiligi: [`backend/app/j1939/`](backend/app/j1939/)

### Filo Veri Modeli

10 marka × 3 model = **30 arac**, her birine benzersiz bir kaynak adres
(`0x00`–`0x1D`) atanmistir. Her arac icin dokuz PGN'in tanimlayicisi onceden
hesaplanir. Tanim: [`backend/data/vehicles.json`](backend/data/vehicles.json)

```json
{
  "id": "s730", "name": "S 730", "segment": "Uzun Yol",
  "source_address": 10, "max_speed_kmh": 95, "power_hp": 730,
  "powertrain": "diesel", "gear_count": 12,
  "idle_rpm": 550, "max_rpm": 1900, "battery_kwh": 2.4
}
```

Aktarma organi dagilimi: 16 dizel, 8 hibrit, 6 elektrikli. Dizel araclarda
SPN 5464 starter akusunu temsil eder (alternator sarjda tutar); elektrikli ve
hibritlerde surus bataryasidir ve yuke gore bosalir, frende rejenerasyonla
kismen dolar.

---

## 2. Backend / Simulator

| Modul | Sorumluluk |
|---|---|
| `app/j1939/core.py` | CAN ID kurulumu, bit paketleme, SPN olcekleme, cerceve nesnesi |
| `app/j1939/messages.py` | Dokuz PGN'in kurucu/cozucu fonksiyonlari ve kayit defteri |
| `app/fleet.py` | `vehicles.json` okuma, kaynak adres benzersizlik dogrulamasi |
| `app/simulator.py` | 100 ms dongu, arac dinamigi, PGN bazli yayin periyotlari |
| `app/hub.py` | WebSocket yayini; istemci basina kuyruk, yavas istemci akisi bloke etmez |
| `app/main.py` | REST uclari, WebSocket komut kanali, yasam dongusu |

### Simulasyon modeli

- **Hiz**: ivme/yavaslama sinirlariyla hedefe rampalanir; fren pedali basinci
  yavaslamayi olcekler.
- **Gaz pedali**: ya dogrudan enjekte edilir (hedef hiz pedaldan turetilir) ya
  da hiz enjeksiyonunda hiz hatasindan turetilir — gosterge her iki durumda da
  tutarli kalir.
- **Vites**: otomatik sanziman gibi hiza gore secilir (model basina 2–16
  kademe); P/R/N/D kademesi elle secilebilir, vites de sabitlenebilir.
- **Motor devri**: vites araligi icinde yukselir, vites yukseltmede duser —
  klasik testere disi deseni. Elektriklilerde tek kademeli, rolanti yoktur.
- **Batarya**: motor yuku ve guc degerine gore tuketilir, frende rejenerasyon.

Her tick'te cevrimici arac basina 4 cerceve (HVBATT saniyede bir eklenir):
30 arac × 10 Hz × 4 mesaj ≈ **1200 frame/s**. Cevrimdisi yapilan arac hatta
hic mesaj basmaz.

> Not: EEC2'nin standart periyodu 50 ms'dir; simulasyon tick'i 100 ms oldugu
> icin bu mesaj da 100 ms'de bir yayinlanir. `SIM_TICK_MS=50` verilerek
> gercek periyoda cikilabilir.

### REST Uclari

| Metot | Uc | Aciklama |
|---|---|---|
| `GET` | `/api/health` | Servis durumu |
| `GET` | `/api/meta` | Mesaj tanimlari ve arayuz sinirlari |
| `GET` | `/api/stats` | Frame sayaci, bus yuku, istemci sayisi |
| `GET` | `/api/vehicles` | Marka bazli gruplanmis filo + durumlar |
| `GET` | `/api/vehicles/{id}` | Tek arac tanimi ve durumu |
| `POST` | `/api/vehicles/{id}/speed` | **SPN 84** hiz enjeksiyonu |
| `POST` | `/api/vehicles/{id}/accelerator` | **SPN 91** gaz pedali (%) |
| `POST` | `/api/vehicles/{id}/brake-pedal` | **SPN 521** fren pedali (%) |
| `POST` | `/api/vehicles/{id}/gear-range` | **SPN 162/163** kademe (P/R/N/D) |
| `POST` | `/api/vehicles/{id}/gear` | **SPN 523/524** vites numarasi |
| `POST` | `/api/vehicles/{id}/battery` | **SPN 5464/5465** SOC ve SOH |
| `POST` | `/api/vehicles/{id}/brake` | SPN 597 fren anahtari (ac/kapa) |
| `POST` | `/api/vehicles/{id}/parking-brake` | SPN 70 el freni |
| `POST` | `/api/vehicles/{id}/cruise` | SPN 595 / 86 cruise control |
| `POST` | `/api/vehicles/{id}/mode` | `manual` / `auto` / `idle` |
| `POST` | `/api/vehicles/{id}/online` | ECU'yu hatta al / hattan cikar |
| `POST` | `/api/fleet/command` | `stop_all` / `auto_all` / `manual_all` / `resume_all` |
| `GET` | `/api/j1939/messages` | Desteklenen PGN katalogu |
| `POST` | `/api/j1939/encode` | Durum degistirmeden cerceve uret |
| `POST` | `/api/j1939/decode` | `ID#DATA` cerceveyi cozumle |

```bash
# Gaz pedalini %65'e getir
curl -X POST http://localhost:8000/api/vehicles/scania-s730/accelerator \
     -H 'Content-Type: application/json' -d '{"pedal_pct": 65}'

# 8. viteste bir ETC2 cercevesi uret
curl -X POST http://localhost:8000/api/j1939/encode \
     -H 'Content-Type: application/json' \
     -d '{"pgn":61445,"signals":{"current_gear":8,"selected_gear":8,"current_range":"D"}}'

# Ham cerceveyi cozumle
curl -X POST http://localhost:8000/api/j1939/decode \
     -H 'Content-Type: application/json' -d '{"frame": "18FD9B0A#C3F0FFFFFFFFFFFF"}'
```

### WebSocket Protokolu — `ws://localhost:8080/ws`

**Sunucu → istemci**

```jsonc
// baglanti aninda
{ "type": "snapshot", "brands": [...], "states": {...}, "meta": { "messages": [...] } }

// her 100 ms
{
  "type": "telemetry", "seq": 152, "ts": 1788335393.02,
  // filo geneli, sikistirilmis:
  // [id, hiz, gaz%, fren%, vites, kademe, rpm, soc%, soh%, ccvs1_hex, tx]
  "t": [["scania-s730", 88.1, 42.0, 0.0, 11, "D", 1620, 97.2, 94.0, "F3184F...", 5250]],
  // abone olunan araclar icin tam cerceveler (cozulmus sinyallerle)
  "frames": [{ "acronym": "EEC2", "candump": "0CF0050A#...", "signals": {...} }],
  "stats": {...},   // saniyede bir
  "states": {...}   // saniyede bir - coklu istemci senkronu
}
```

**Istemci → sunucu**

```jsonc
{ "type": "subscribe",       "vehicle_ids": ["scania-s730"] }
{ "type": "set_speed",       "vehicle_id": "scania-s730", "speed_kmh": 120, "instant": false }
{ "type": "set_accelerator", "vehicle_id": "scania-s730", "pedal_pct": 65 }
{ "type": "set_brake_pedal", "vehicle_id": "scania-s730", "pedal_pct": 40 }
{ "type": "set_gear_range",  "vehicle_id": "scania-s730", "gear_range": "R" }
{ "type": "set_gear",        "vehicle_id": "scania-s730", "gear": 8 }
{ "type": "set_battery",     "vehicle_id": "scania-s730", "soc_pct": 62, "soh_pct": 93 }
{ "type": "set_cruise",      "vehicle_id": "scania-s730", "active": true, "set_speed_kmh": 88 }
{ "type": "fleet_command",   "action": "stop_all" }
{ "type": "ping" }
```

---

## 3. Web Arayuzu

Derleme adimi gerektirmeyen (vanilla JS) bir panel.

| Dosya | Icerik |
|---|---|
| `js/api.js` | REST istemcisi, uc nokta cozumleme |
| `js/socket.js` | Ussel geri cekilmeli yeniden baglanan WebSocket istemcisi |
| `js/vehicle-art.js` | Segmente gore satir ici SVG arac gorselleri |
| `js/audio.js` | Web Audio API ile motor sesi sentezi |
| `js/ui.js` | DOM olusturma/guncelleme |
| `js/app.js` | Durum yonetimi, olay baglama, render dongusu |

### Arac kartlari

Her kartta aracin gorseli, marka renginde kabin ve aktarma organi rozeti
(D / HEV / EV) bulunur. Altinda anlik hiz, vites gostergesi, gaz/fren pedali
barlari ve SOC/SOH olcerleri yer alir.

### Arac gorselleri

Iki kaynak desteklenir ve **varsayilan SVG'dir**:

| Durum | Sonuc |
|---|---|
| `image` alani bos | Segmente gore yerinde cizilen SVG siluet |
| `image` dolu, dosya var | Gercek gorsel (`<img>`) |
| `image` dolu, dosya yok | Sessizce SVG cizime doner — bozuk gorsel ikonu cikmaz |

Boylece proje kutudan ciktigi gibi hicbir dis kaynaga bagimli olmadan,
internet olmadan ve siki bir CSP altinda calisir; gorsel eklemek istege
baglidir.

**Gorsel eklemek icin** dosyayi `frontend/img/` dizinine koyun ve
`backend/data/vehicles.json` icinde ilgili modelin `image` alanini doldurun:

```json
{
  "id": "actros",
  "name": "Actros",
  "image": "img/mercedes-benz-actros.webp",
  "image_credit": "Foto: Ad Soyad — CC BY-SA 4.0"
}
```

`image_credit` doldurulursa secili aracin gorselinin altinda gosterilir;
Creative Commons lisansli gorsellerde atif zorunludur.

Onerilen en-boy orani 320 × 132 (kart alaniyla ayni), 640 × 264 piksel
cozunurluk ve `.webp` bicimi yeterlidir. Ayrintilar:
[`frontend/img/README.md`](frontend/img/README.md)

> **Telif:** Buraya yalnizca kullanim hakkina sahip oldugunuz gorselleri
> koyun. Uretici sitelerindeki (Mercedes-Benz, Volvo, MAN vb.) basin ve
> tanitim fotograflari telif hakkiyla korunur; herkese acik bir depoda
> yayinlanmalari ihlal olusturur. Guvenli kaynaklar: kendi cektiginiz
> fotograflar, Wikimedia Commons uzerindeki Creative Commons lisansli
> gorseller veya ureticiden yazili izin aldiginiz basin kiti gorselleri.

### Motor sesi (Web Audio API)

Dis medya dosyasi kullanilmaz; ses tamamen sentezlenir:

```
osc1 (testere disi)  ateşleme temel frekansi   \
osc2 (kare, 0.5x)    alt harmonik              -> alcak geciren filtre -> cikis
gurultu (bant gecis) turbo / hava akisi        /
```

- **Frekans**, motor devrinden hesaplanir: `f = rpm/60 × (silindir/2)`.
  Devir vitesle testere disi hareket ettigi icin ton her vites yukseltmesinde
  duser ve tekrar tirmanir — gercek bir kamyonun davranisi.
- **Kazanc**, motor yuku (SPN 92) ve hiza baglidir; hizlandikca yukselir,
  frende duser.
- **Elektrikli araclarda** ateşleme tonu yerine hizla tizlesen invertor
  uultusu (triangle osilator) kullanilir.
- Tarayici otomatik oynatma politikasi geregi ses yalnizca **"Motor Sesi"
  butonuna tiklandiginda** baslar.

| Hiz | Vites | Devir | Osilator | Kazanc |
|---|---|---|---|---|
| 0 km/h | 1 | 554 rpm | 28 Hz | 0.13 |
| 30 km/h | 4 | 1672 rpm | 82 Hz | 0.10 |
| 60 km/h | 8 | 1375 rpm | 69 Hz | 0.19 |
| 90 km/h | 12 | 1088 rpm | 55 Hz | 0.24 |

### Kontroller

Hiz (SPN 84) slider + nümerik giris + hazir degerler, gaz pedali (SPN 91),
fren pedali (SPN 521) + tam fren, vites/kademe secimi (SPN 523 / 162 / 163),
SOC ve SOH slider'lari (SPN 5464 / 5465), el freni, cruise, ECU cevrimici
anahtari, surus modu ve motor sesi ac/kapa + ses seviyesi.

10 Hz'de 30 arac guncellenirken DOM yeniden olusturulmaz; yalnizca degisen
hucre degerleri yazilir ve boyama `requestAnimationFrame` ile birlestirilir.

---

## 4. Docker

| Servis | Imaj | Port |
|---|---|---|
| `backend_telemetry` | `python:3.12-slim` | 8000 |
| `frontend` | `nginx:1.27-alpine` | 8080 → 80 |

`frontend`, `backend_telemetry` saglikli olana kadar bekler. Backend imaji
ayrica bir `test` katmani icerir:

```bash
docker build --target test -t j1939/backend:test ./backend && docker run --rm j1939/backend:test
```

## 5. CI/CD

[`.github/workflows/main.yml`](.github/workflows/main.yml) dort asamadan olusur:

1. **backend-test** — `ruff check` + `ruff format --check` + `pytest`
2. **frontend-check** — JavaScript sozdizimi ve varlik dogrulamasi
3. **docker-build** — imajlarin derlenmesi, testlerin konteyner icinde kosmasi
4. **compose-smoke-test** — `docker compose up` sonrasi dokuz PGN'in kodlanmasi,
   sinyal enjeksiyonu ve WebSocket akisinin uctan uca dogrulanmasi

> Workflow dosyasi depo kokunde `.github/workflows/` altinda bulunmalidir.
> Proje bir alt dizinde duruyorsa dosyanin basindaki `PROJECT_DIR` degerini
> guncelleyin.

## 6. Testler

```bash
cd backend && python -m pytest -v
```

| Dosya | Kapsam |
|---|---|
| `tests/test_j1939.py` | Bes mesajin CAN ID'si, SPN cozunurlukleri, byte sirasi, kayit defteri |
| `tests/test_simulator.py` | Pedal, vites, fren, batarya, devir davranisi ve yayin periyotlari |
| `tests/test_api.py` | REST uclari, dogrulama hatalari, WebSocket akisi ve komutlari |

## 7. Yapilandirma

| Degisken | Varsayilan | Aciklama |
|---|---|---|
| `SIM_TICK_MS` | `100` | Simulasyon periyodu |
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
│   │   ├── j1939/
│   │   │   ├── core.py       # CAN ID, bit paketleme, SPN olcekleme
│   │   │   └── messages.py   # CCVS1 / EEC2 / ETC2 / EBC1 / HVBATT
│   │   ├── fleet.py          # filo tanimi ve dogrulama
│   │   ├── simulator.py      # arac dinamigi ve yayin periyotlari
│   │   ├── hub.py            # WebSocket yayin katmani
│   │   ├── models.py         # Pydantic semalari
│   │   ├── config.py         # ortam degiskeni ayarlari
│   │   └── main.py           # FastAPI uygulamasi
│   ├── data/vehicles.json    # 10 marka × 3 model
│   ├── tests/
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   ├── js/{api,socket,vehicle-art,audio,ui,app}.js
│   ├── img/brands/           # 30 aracin gercek gorselleri (Wikimedia + resmi siteler)
│   ├── nginx.conf
│   └── Dockerfile
├── .github/workflows/main.yml
├── docker-compose.yml
├── Makefile
└── .env.example
```
