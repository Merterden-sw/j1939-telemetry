/* =========================================================================
   Otomatik yeniden baglanan WebSocket istemcisi.

   Kullanim:
     const socket = new J1939Socket(J1939Api.WS_URL);
     socket.on("telemetry", (msg) => ...);
     socket.onState((state) => ...);   // "connecting" | "open" | "closed"
     socket.connect();
     socket.send({ type: "set_speed", vehicle_id: "man-tgx", speed_kmh: 80 });
   ========================================================================= */
(function (global) {
  "use strict";

  const RECONNECT_MIN_MS = 500;
  const RECONNECT_MAX_MS = 8000;
  const HEARTBEAT_MS = 20000;

  class J1939Socket {
    constructor(url) {
      this.url = url;
      this.ws = null;
      this.handlers = new Map();
      this.stateHandlers = [];
      this.retry = 0;
      this.closedByUser = false;
      this._heartbeat = null;
      this._reconnectTimer = null;
    }

    on(type, handler) {
      if (!this.handlers.has(type)) this.handlers.set(type, []);
      this.handlers.get(type).push(handler);
      return this;
    }

    onState(handler) {
      this.stateHandlers.push(handler);
      return this;
    }

    _emitState(state, detail) {
      this.stateHandlers.forEach((handler) => handler(state, detail));
    }

    connect() {
      this.closedByUser = false;
      this._emitState("connecting");

      try {
        this.ws = new WebSocket(this.url);
      } catch (error) {
        this._scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this.retry = 0;
        this._emitState("open");
        this._heartbeat = setInterval(() => this.send({ type: "ping" }), HEARTBEAT_MS);
      };

      this.ws.onmessage = (event) => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch (error) {
          console.warn("Cozumlenemeyen mesaj:", event.data);
          return;
        }
        (this.handlers.get(message.type) || []).forEach((handler) => handler(message));
        (this.handlers.get("*") || []).forEach((handler) => handler(message));
      };

      this.ws.onclose = () => {
        clearInterval(this._heartbeat);
        this._emitState("closed");
        if (!this.closedByUser) this._scheduleReconnect();
      };

      this.ws.onerror = () => { /* onclose zaten yeniden baglanmayi tetikler */ };
    }

    _scheduleReconnect() {
      clearTimeout(this._reconnectTimer);
      const delay = Math.min(RECONNECT_MIN_MS * 2 ** this.retry, RECONNECT_MAX_MS);
      this.retry += 1;
      this._emitState("connecting", `${Math.round(delay / 100) / 10} sn sonra yeniden denenecek`);
      this._reconnectTimer = setTimeout(() => this.connect(), delay);
    }

    get isOpen() {
      return this.ws && this.ws.readyState === WebSocket.OPEN;
    }

    send(payload) {
      if (!this.isOpen) return false;
      this.ws.send(JSON.stringify(payload));
      return true;
    }

    close() {
      this.closedByUser = true;
      clearInterval(this._heartbeat);
      clearTimeout(this._reconnectTimer);
      if (this.ws) this.ws.close();
    }
  }

  global.J1939Socket = J1939Socket;
})(window);
