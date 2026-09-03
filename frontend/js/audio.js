/* =========================================================================
   Dinamik motor sesi - Web Audio API ile sentez.

   Dis medya dosyasi kullanilmaz; ses tamamen osilator ve gurultu
   uretecinden olusturulur:

     osc1 (testere disi)  ateşleme temel frekansi        \
     osc2 (kare, 0.5x)    alt harmonik / govde titresimi  > alcak geciren
     gurultu (bant gecis) turbo ve hava akisi            /   filtre -> cikis

   Frekans, aracin motor devrinden (SPN 84 hizindan ve vitesten turetilen
   devirden) hesaplanir; kazanc ise motor yukune (SPN 92) ve gaz pedaline
   (SPN 91) baglidir. Boylece hizlanmada ses tizlesir ve yukselir, vites
   yukseltmesinde devir dustugu icin ton bir anda asagi iner.

   Tarayicilar ses baslatmayi kullanici etkilesimine bagladigi icin
   enable() mutlaka bir tikla cagrilmalidir.
   ========================================================================= */
(function (global) {
  "use strict";

  const SMOOTHING_S = 0.08;      // parametre gecis sabiti (tiklama sesini onler
  const MASTER_CEILING = 0.55;   // toplam kazanc tavani (isitme guvenligi)
  const CYLINDER_FACTOR = 3;     // 6 silindir 4 zamanli: rpm/60 * (6/2)

  class EngineAudio {
    constructor() {
      this.ctx = null;
      this.nodes = null;
      this.enabled = false;
      this.volume = 0.6;
      this._last = { rpm: 0, load: 0 };
    }

    /** Ses zincirini kurar. Kullanici etkilesiminden cagrilmalidir. */
    async enable() {
      if (this.enabled) return true;

      const AudioCtx = global.AudioContext || global.webkitAudioContext;
      if (!AudioCtx) {
        console.warn("Web Audio API bu tarayicida desteklenmiyor.");
        return false;
      }

      if (!this.ctx) {
        this.ctx = new AudioCtx();
        this._build();
      }
      // Otomatik oynatma politikasi: baglami elle devam ettirmek gerekir.
      if (this.ctx.state === "suspended") await this.ctx.resume();

      this.enabled = true;
      return true;
    }

    /** Sesi susturur (baglam korunur, tekrar acmak ucuzdur). */
    disable() {
      this.enabled = false;
      if (this.nodes) {
        this.nodes.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.05);
      }
    }

    toggle() {
      return this.enabled ? (this.disable(), false) : (this.enable(), true);
    }

    setVolume(value) {
      this.volume = Math.min(Math.max(Number(value) || 0, 0), 1);
    }

    /** Ses grafigini bir kez kurar; osilatorler surekli calisir, kazancla kisilir. */
    _build() {
      const ctx = this.ctx;

      const master = ctx.createGain();
      master.gain.value = 0;
      master.connect(ctx.destination);

      const lowpass = ctx.createBiquadFilter();
      lowpass.type = "lowpass";
      lowpass.frequency.value = 600;
      lowpass.Q.value = 0.8;
      lowpass.connect(master);

      // Temel ateşleme tonu
      const osc1 = ctx.createOscillator();
      osc1.type = "sawtooth";
      osc1.frequency.value = 60;
      const gain1 = ctx.createGain();
      gain1.gain.value = 0.5;
      osc1.connect(gain1).connect(lowpass);

      // Alt harmonik - govde titresimi
      const osc2 = ctx.createOscillator();
      osc2.type = "square";
      osc2.frequency.value = 30;
      const gain2 = ctx.createGain();
      gain2.gain.value = 0.22;
      osc2.connect(gain2).connect(lowpass);

      // Elektrikli araclarin invertor uultusu
      const whine = ctx.createOscillator();
      whine.type = "triangle";
      whine.frequency.value = 400;
      const whineGain = ctx.createGain();
      whineGain.gain.value = 0;
      whine.connect(whineGain).connect(master);

      // Turbo / hava akisi: donguye alinmis beyaz gurultu
      const noiseBuffer = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
      const channel = noiseBuffer.getChannelData(0);
      for (let i = 0; i < channel.length; i += 1) channel[i] = Math.random() * 2 - 1;

      const noise = ctx.createBufferSource();
      noise.buffer = noiseBuffer;
      noise.loop = true;
      const noiseFilter = ctx.createBiquadFilter();
      noiseFilter.type = "bandpass";
      noiseFilter.frequency.value = 900;
      noiseFilter.Q.value = 0.7;
      const noiseGain = ctx.createGain();
      noiseGain.gain.value = 0;
      noise.connect(noiseFilter).connect(noiseGain).connect(master);

      osc1.start();
      osc2.start();
      whine.start();
      noise.start();

      this.nodes = { master, lowpass, osc1, osc2, gain1, gain2, whine, whineGain,
                     noise, noiseFilter, noiseGain };
    }

    /**
     * Telemetriye gore sesi gunceller.
     * @param {object} t - { rpm, maxRpm, load, speed, maxSpeed, powertrain, online }
     */
    update(t) {
      if (!this.enabled || !this.nodes || !this.ctx) return;

      const ctx = this.ctx;
      const now = ctx.currentTime;
      const n = this.nodes;
      const set = (param, value) => param.setTargetAtTime(value, now, SMOOTHING_S);

      // Cevrimdisi arac: hatta mesaj yok, motor da yok.
      if (!t.online) {
        set(n.master.gain, 0);
        return;
      }

      const rpm = Math.max(t.rpm || 0, 0);
      const maxRpm = Math.max(t.maxRpm || 2000, 1);
      const load = Math.min(Math.max(t.load || 0, 0), 125) / 100;
      const speedRatio = Math.min((t.speed || 0) / (t.maxSpeed || 180), 1);
      const rpmRatio = Math.min(rpm / maxRpm, 1);

      if (t.powertrain === "electric") {
        // Elektrikli: ates leme yok, hiza gore yukselen invertor uultusu.
        set(n.gain1.gain, 0.02);
        set(n.gain2.gain, 0);
        set(n.osc1.frequency, 40 + rpmRatio * 30);
        set(n.whineGain.gain, rpm > 1 ? 0.06 + rpmRatio * 0.16 : 0);
        set(n.whine.frequency, 220 + rpmRatio * 1900);
        set(n.lowpass.frequency, 1200 + rpmRatio * 3000);
        set(n.noiseFilter.frequency, 1800 + speedRatio * 2500);
        set(n.noiseGain.gain, 0.01 + speedRatio * 0.045);
      } else {
        // Icten yanmali: ateşleme frekansi = rpm/60 * (silindir/2)
        const fundamental = Math.min(Math.max((rpm / 60) * CYLINDER_FACTOR, 18), 260);
        set(n.osc1.frequency, fundamental);
        set(n.osc2.frequency, fundamental / 2);
        set(n.gain1.gain, 0.34 + load * 0.3);
        set(n.gain2.gain, 0.16 + load * 0.16);
        set(n.whineGain.gain, 0);
        // Yuk arttikca filtre acilir: ses "sertlesir".
        set(n.lowpass.frequency, 380 + rpmRatio * 2200 + load * 900);
        set(n.noiseFilter.frequency, 700 + rpmRatio * 1600);
        set(n.noiseGain.gain, 0.015 + load * 0.055);
      }

      // Toplam ses seviyesi: rolantide duyulur, yukte yukselir.
      const level = (0.1 + load * 0.55 + speedRatio * 0.25) * this.volume;
      set(n.master.gain, Math.min(level, MASTER_CEILING));

      this._last = { rpm, load };
    }

    /** Kaynaklari serbest birakir (sayfa kapanirken). */
    async close() {
      this.enabled = false;
      if (this.ctx) {
        await this.ctx.close().catch(() => {});
        this.ctx = null;
        this.nodes = null;
      }
    }
  }

  global.J1939EngineAudio = EngineAudio;
})(window);
