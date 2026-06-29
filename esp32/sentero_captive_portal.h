#pragma once

#include "esphome.h"
#include "esphome/components/captive_portal/captive_portal.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include "esphome/components/wifi/wifi_component.h"
#include "sentero_portal_logo.h"

#include <cstring>

static constexpr const char *SENTERO_PORTAL_TAG = "sentero.portal";

class SenteroCaptivePortal : public AsyncWebHandler, public esphome::Component {
 public:
  bool register_handler() {
    if (registered_) return true;
    auto *base = esphome::web_server_base::global_web_server_base;
    if (base == nullptr) return false;

    base->add_handler_without_auth(this);
    registered_ = true;
    ESP_LOGI(SENTERO_PORTAL_TAG, "Sentero captive portal UI registered");
    return true;
  }

  bool canHandle(AsyncWebServerRequest *request) const override {
    auto *portal = esphome::captive_portal::global_captive_portal;
    return portal != nullptr && portal->is_active() && request->method() == HTTP_GET;
  }

  void handleRequest(AsyncWebServerRequest *request) override {
#ifdef USE_ESP32
    char url_buf[AsyncWebServerRequest::URL_BUF_SIZE];
    StringRef url = request->url_to(url_buf);
#else
    const auto &url = request->url();
#endif

    if (url == ESPHOME_F("/config.json")) {
      handle_config_(request);
      return;
    }
    if (url == ESPHOME_F("/scan.json")) {
      handle_scan_(request);
      return;
    }
    if (url == ESPHOME_F("/wifisave")) {
      handle_wifi_save_(request);
      return;
    }
    if (url == ESPHOME_F("/sentero-logo.jpg")) {
      handle_logo_(request);
      return;
    }
    if (url == ESPHOME_F("/favicon.ico")) {
      request->send(204, ESPHOME_F("text/plain"), ESPHOME_F(""));
      return;
    }

    handle_index_(request);
  }

 private:
  bool registered_{false};

  static constexpr const char INDEX_HTML[] PROGMEM = R"HTML(<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>Sentero Setup</title>
  <style>
    :root {
      color-scheme: dark light;
      --page: #0b0d0b;
      --surface: #f4f1e8;
      --surface-2: #e4e8dc;
      --ink: #172019;
      --muted: #60705f;
      --accent: #6f8f73;
      --accent-dark: #4e6f51;
      --line: rgba(23, 32, 25, .16);
      --danger: #aa4d3f;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        linear-gradient(180deg, rgba(11,13,11,.62), rgba(11,13,11,.96)),
        url("/sentero-logo.jpg") center top / min(920px, 160vw) auto no-repeat,
        var(--page);
      color: var(--surface);
    }
    button, input, select { font: inherit; }
    .shell {
      width: min(920px, 100%);
      min-height: 100svh;
      margin: 0 auto;
      padding: 24px;
      display: grid;
      grid-template-columns: minmax(0, .95fr) minmax(320px, 1.05fr);
      gap: 24px;
      align-items: end;
    }
    .brand {
      min-height: 440px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      padding-bottom: 18px;
    }
    .brand img {
      width: min(320px, 72vw);
      height: auto;
      display: block;
      margin-bottom: 22px;
    }
    .kicker {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 16px;
    }
    .kicker span {
      border: 1px solid rgba(244, 241, 232, .25);
      background: rgba(244, 241, 232, .08);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 13px;
      color: rgba(244, 241, 232, .86);
    }
    h1 {
      margin: 0;
      font-size: 42px;
      line-height: 1.04;
      font-weight: 680;
      letter-spacing: 0;
    }
    .brand p {
      margin: 14px 0 0;
      max-width: 34rem;
      color: rgba(244, 241, 232, .76);
      line-height: 1.55;
    }
    .panel {
      background: var(--surface);
      color: var(--ink);
      border-radius: var(--radius);
      border: 1px solid rgba(255,255,255,.18);
      box-shadow: 0 20px 60px rgba(0,0,0,.30);
      padding: 20px;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    .panel-title {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      font-weight: 700;
    }
    .state {
      min-width: 76px;
      text-align: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--accent-dark);
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 700;
    }
    label {
      display: block;
      margin: 14px 0 7px;
      color: #344336;
      font-size: 13px;
      font-weight: 700;
    }
    input, select {
      width: 100%;
      min-height: 46px;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background: #fffdf7;
      color: var(--ink);
      padding: 0 12px;
      outline: none;
    }
    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(111, 143, 115, .22);
    }
    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }
    .ghost, .toggle {
      min-height: 46px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #eef0e8;
      color: #253227;
      padding: 0 13px;
      cursor: pointer;
    }
    .ghost:disabled, .toggle:disabled {
      opacity: .62;
      cursor: wait;
    }
    .password {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
    }
    .primary {
      width: 100%;
      min-height: 48px;
      margin-top: 18px;
      border: 0;
      border-radius: var(--radius);
      background: var(--accent-dark);
      color: #fff;
      font-weight: 780;
      cursor: pointer;
    }
    .primary:disabled {
      opacity: .68;
      cursor: wait;
    }
    .message {
      min-height: 22px;
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }
    .message.error { color: var(--danger); }
    .networks {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .network {
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fffdf7;
      color: var(--ink);
      padding: 8px 10px;
      cursor: pointer;
      text-align: left;
      overflow: hidden;
    }
    .network strong {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
    }
    .network span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
      color: var(--muted);
      font-size: 12px;
    }
    .meta span {
      border-top: 1px solid var(--line);
      padding-top: 8px;
      min-width: 0;
    }
    @media (max-width: 760px) {
      .shell {
        padding: 16px;
        grid-template-columns: 1fr;
        align-items: stretch;
      }
      .brand {
        min-height: 300px;
        padding-top: 72px;
      }
      h1 { font-size: 34px; }
      .panel { padding: 16px; }
      .networks { grid-template-columns: 1fr; }
      .row, .password { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="brand">
      <img src="/sentero-logo.jpg" alt="Sentero">
      <h1>WLAN einrichten</h1>
      <p>Verbinde den Praesenzsensor mit deinem Heimnetz.</p>
    </section>

    <section class="panel" aria-label="WLAN Setup">
      <div class="panel-head">
        <h2 class="panel-title">Netzwerk</h2>
        <span class="state" id="state">Bereit</span>
      </div>

      <form id="wifiForm" action="/wifisave" method="get">
        <label for="ssid">SSID</label>
        <div class="row">
          <select id="networkSelect" aria-label="Gefundene Netzwerke">
            <option value="">Netzwerk waehlen</option>
          </select>
          <button class="ghost" type="button" id="refresh">Scan</button>
        </div>

        <input id="ssid" name="ssid" autocomplete="username" required placeholder="SSID">

        <label for="psk">Passwort</label>
        <div class="password">
          <input id="psk" name="psk" type="password" autocomplete="current-password" placeholder="WLAN Passwort">
          <button class="toggle" type="button" id="togglePass">Anzeigen</button>
        </div>

        <button class="primary" type="submit" id="submit">Verbinden</button>
        <p class="message" id="message" role="status"></p>
      </form>

      <div class="networks" id="networkList"></div>
      <div class="meta">
        <span id="deviceName">Sensor</span>
        <span id="deviceMac">MAC wird geladen</span>
      </div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const form = $("wifiForm");
    const ssid = $("ssid");
    const psk = $("psk");
    const select = $("networkSelect");
    const list = $("networkList");
    const message = $("message");
    const state = $("state");
    const submit = $("submit");
    const refresh = $("refresh");

    function setMessage(text, error = false) {
      message.textContent = text;
      message.className = error ? "message error" : "message";
    }

    function quality(rssi) {
      if (rssi >= -55) return "Sehr stark";
      if (rssi >= -67) return "Stark";
      if (rssi >= -75) return "Mittel";
      return "Schwach";
    }

    function chooseNetwork(name) {
      ssid.value = name;
      select.value = name;
      psk.focus();
    }

    async function loadConfig() {
      state.textContent = "Scan";
      try {
        const response = await fetch("/config.json", { cache: "no-store" });
        if (!response.ok) throw new Error("scan_failed");
        const data = await response.json();
        $("deviceName").textContent = data.name || "C1001 mmWave";
        $("deviceMac").textContent = data.mac || "";

        const aps = (data.aps || [])
          .filter((ap) => ap && ap.ssid)
          .sort((a, b) => (b.rssi || -100) - (a.rssi || -100));

        select.innerHTML = '<option value="">Netzwerk waehlen</option>';
        list.innerHTML = "";

        aps.forEach((ap) => {
          const option = document.createElement("option");
          option.value = ap.ssid;
          option.textContent = ap.ssid;
          select.appendChild(option);

          const button = document.createElement("button");
          button.type = "button";
          button.className = "network";
          button.innerHTML = `<strong></strong><span>${quality(ap.rssi)} · ${ap.lock ? "gesichert" : "offen"}</span>`;
          button.querySelector("strong").textContent = ap.ssid;
          button.addEventListener("click", () => chooseNetwork(ap.ssid));
          list.appendChild(button);
        });

        state.textContent = aps.length ? `${aps.length} WLAN` : "Manuell";
        if (!aps.length) {
          setMessage("Keine Netzwerke gefunden. Scan erneut starten oder SSID manuell eintragen.");
        } else {
          setMessage("");
        }
      } catch (err) {
        state.textContent = "Manuell";
        setMessage("Scan nicht verfuegbar. SSID manuell eintragen.", true);
      }
    }

    async function startScan() {
      refresh.disabled = true;
      state.textContent = "Scan";
      setMessage("Scan laeuft. Die Verbindung zum Setup-WLAN kann kurz pausieren...");
      try {
        const response = await fetch("/scan.json", { cache: "no-store" });
        if (!response.ok) throw new Error("scan_start_failed");
        await new Promise((resolve) => setTimeout(resolve, 4500));
        await loadConfig();
      } catch (err) {
        state.textContent = "Manuell";
        setMessage("Scan konnte nicht gestartet werden. SSID manuell eintragen.", true);
      } finally {
        refresh.disabled = false;
      }
    }

    select.addEventListener("change", () => {
      if (select.value) chooseNetwork(select.value);
    });

    refresh.addEventListener("click", startScan);

    $("togglePass").addEventListener("click", () => {
      const hidden = psk.type === "password";
      psk.type = hidden ? "text" : "password";
      $("togglePass").textContent = hidden ? "Ausblenden" : "Anzeigen";
    });

    form.addEventListener("submit", async (event) => {
      if (!window.fetch) return;
      event.preventDefault();
      if (!ssid.value.trim()) {
        setMessage("SSID fehlt.", true);
        ssid.focus();
        return;
      }

      submit.disabled = true;
      state.textContent = "Speichert";
      setMessage("WLAN wird gespeichert...");

      const params = new URLSearchParams({ ssid: ssid.value.trim(), psk: psk.value });
      try {
        const response = await fetch(`/wifisave?${params.toString()}`, { cache: "no-store" });
        if (!response.ok) throw new Error("save_failed");
        state.textContent = "Gespeichert";
        setMessage("Gespeichert. Der Sensor verbindet sich jetzt neu.");
      } catch (err) {
        state.textContent = "Fehler";
        submit.disabled = false;
        setMessage("Speichern fehlgeschlagen.", true);
      }
    });

    loadConfig();
  </script>
</body>
</html>)HTML";

  void print_json_string_(AsyncResponseStream *stream, const char *value) {
    stream->print('"');
    if (value != nullptr) {
      for (const char *cursor = value; *cursor != '\0'; cursor++) {
        const uint8_t ch = static_cast<uint8_t>(*cursor);
        if (*cursor == '"' || *cursor == '\\') {
          stream->print('\\');
          stream->print(*cursor);
        } else if (ch < 0x20) {
          stream->printf("\\u%04x", ch);
        } else {
          stream->print(*cursor);
        }
      }
    }
    stream->print('"');
  }

  void handle_config_(AsyncWebServerRequest *request) {
    AsyncResponseStream *stream = request->beginResponseStream(ESPHOME_F("application/json"));
    stream->addHeader(ESPHOME_F("cache-control"), ESPHOME_F("public, max-age=0, must-revalidate"));

    char mac_s[18];
    const char *mac_str = get_mac_address_pretty_into_buffer(mac_s);
    stream->print(ESPHOME_F("{\"mac\":"));
    print_json_string_(stream, mac_str);
    stream->print(ESPHOME_F(",\"name\":"));
    print_json_string_(stream, esphome::App.get_name().c_str());
    stream->print(ESPHOME_F(",\"aps\":["));

    bool first = true;
    for (auto &scan : esphome::wifi::global_wifi_component->get_scan_result()) {
      if (scan.get_is_hidden()) continue;
      if (!first) stream->print(',');
      first = false;
      stream->print(ESPHOME_F("{\"ssid\":"));
      print_json_string_(stream, scan.get_ssid().c_str());
      stream->printf(",\"rssi\":%d,\"lock\":%d}", scan.get_rssi(), scan.get_with_auth());
    }

    stream->print(ESPHOME_F("]}"));
    request->send(stream);
  }

  void handle_scan_(AsyncWebServerRequest *request) {
    auto *wifi = esphome::wifi::global_wifi_component;
    if (wifi == nullptr) {
      request->send(503, ESPHOME_F("application/json"), ESPHOME_F("{\"ok\":false,\"error\":\"wifi_unavailable\"}"));
      return;
    }

    wifi->set_keep_scan_results(true);
    wifi->start_scanning();
    request->send(202, ESPHOME_F("application/json"), ESPHOME_F("{\"ok\":true,\"status\":\"scan_started\"}"));
  }

  void handle_wifi_save_(AsyncWebServerRequest *request) {
    const auto &ssid = request->arg("ssid");
    const auto &psk = request->arg("psk");
    if (ssid.length() == 0) {
      request->send(400, ESPHOME_F("text/plain"), ESPHOME_F("Missing SSID"));
      return;
    }

    ESP_LOGI(SENTERO_PORTAL_TAG,
             "Requested WiFi Settings Change:\n"
             "  SSID='%s'\n"
             "  Password=" LOG_SECRET("'%s'"),
             ssid.c_str(), psk.c_str());

#ifdef USE_ESP8266
    esphome::wifi::global_wifi_component->save_wifi_sta(ssid.c_str(), psk.c_str());
#else
    this->defer([ssid, psk]() {
      esphome::wifi::global_wifi_component->save_wifi_sta(ssid.c_str(), psk.c_str());
    });
#endif

    request->send(200, ESPHOME_F("text/plain"), ESPHOME_F("Saved. Connecting..."));
  }

  void handle_logo_(AsyncWebServerRequest *request) {
    auto *response = request->beginResponse(200, ESPHOME_F("image/jpeg"),
                                            SENTERO_PORTAL_LOGO_JPG,
                                            SENTERO_PORTAL_LOGO_JPG_SIZE);
    response->addHeader(ESPHOME_F("cache-control"), ESPHOME_F("public, max-age=31536000, immutable"));
    request->send(response);
  }

  void handle_index_(AsyncWebServerRequest *request) {
    auto *response = request->beginResponse(200, ESPHOME_F("text/html"),
                                            reinterpret_cast<const uint8_t *>(INDEX_HTML),
                                            std::strlen(INDEX_HTML));
    response->addHeader(ESPHOME_F("cache-control"), ESPHOME_F("public, max-age=0, must-revalidate"));
    request->send(response);
  }
};

static SenteroCaptivePortal sentero_captive_portal;

inline void sentero_captive_portal_setup() {
  sentero_captive_portal.register_handler();
}
