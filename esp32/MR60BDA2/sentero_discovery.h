#pragma once

#include "mr60bda2_bridge.h"
#include "esphome.h"
#include "esphome/components/wifi/wifi_component.h"
#include "esphome/components/web_server_base/web_server_base.h"
#include <ArduinoJson.h>
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "mqtt_client.h"
#include "nvs.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include <cstring>

static constexpr const char *SENTERO_NVS_NAMESPACE = "sentero";
static constexpr const char *SENTERO_LOG_TAG = "sentero";
static constexpr const char *SENTERO_MANUFACTURER = "Sentero";
static constexpr const char *SENTERO_DEVICE_MODEL = "MR60BDA2";
static constexpr const char *SENTERO_SENSOR_TYPE = "presence_radar";
static constexpr const char *SENTERO_FIRMWARE_VERSION = "1.0.1";

// ---------------------------------------------------------------------------
// Kleine std::string-Helper als Ersatz fuer Arduino String::trim() /
// toLowerCase() / replace() / startsWith() / endsWith(), die es unter
// framework: esp-idf nicht gibt (dort existiert die Arduino String-Klasse
// gar nicht).
// ---------------------------------------------------------------------------
inline void str_trim(std::string &s) {
  const char *whitespace = " \t\r\n";
  const size_t start = s.find_first_not_of(whitespace);
  if (start == std::string::npos) {
    s.clear();
    return;
  }
  const size_t end = s.find_last_not_of(whitespace);
  s = s.substr(start, end - start + 1);
}

inline void str_to_lower(std::string &s) {
  for (char &c : s) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
}

inline void str_replace_all(std::string &s, const char *from, const char *to) {
  if (from == nullptr || strlen(from) == 0) return;
  size_t pos = 0;
  const size_t from_len = strlen(from);
  while ((pos = s.find(from, pos)) != std::string::npos) {
    s.replace(pos, from_len, to);
    pos += strlen(to);
  }
}

inline bool str_starts_with(const std::string &s, const char *prefix) {
  return s.rfind(prefix, 0) == 0;
}

inline bool str_ends_with(const std::string &s, const char *suffix) {
  const size_t suffix_len = strlen(suffix);
  if (suffix_len > s.length()) return false;
  return s.compare(s.length() - suffix_len, suffix_len, suffix) == 0;
}

inline bool sentero_nvs_get_bool(const char *key, bool fallback = false) {
  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return fallback;
  uint8_t value = fallback ? 1 : 0;
  nvs_get_u8(handle, key, &value);
  nvs_close(handle);
  return value != 0;
}

inline uint16_t sentero_nvs_get_u16(const char *key, uint16_t fallback = 0) {
  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return fallback;
  uint16_t value = fallback;
  nvs_get_u16(handle, key, &value);
  nvs_close(handle);
  return value;
}

inline std::string sentero_nvs_get_string(const char *key, const char *fallback = "") {
  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return std::string(fallback);
  size_t len = 0;
  if (nvs_get_str(handle, key, nullptr, &len) != ESP_OK || len == 0) {
    nvs_close(handle);
    return std::string(fallback);
  }
  std::string value;
  value.resize(len);
  if (nvs_get_str(handle, key, &value[0], &len) != ESP_OK) {
    nvs_close(handle);
    return std::string(fallback);
  }
  nvs_close(handle);
  if (!value.empty() && value.back() == '\0') value.pop_back();
  return std::string(value.c_str());
}

inline void sentero_nvs_put_string(nvs_handle_t handle, const char *key, const char *value) {
  nvs_set_str(handle, key, value == nullptr ? "" : value);
}

inline std::string sentero_uuid_from_bytes(const uint8_t bytes[16]) {
  char uuid[37];
  snprintf(uuid, sizeof(uuid),
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3],
           bytes[4], bytes[5],
           bytes[6], bytes[7],
           bytes[8], bytes[9],
           bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
  return std::string(uuid);
}

inline std::string sentero_mac_fallback_uuid() {
  uint8_t mac[6];
  get_mac_address_raw(mac);

  uint8_t bytes[16] = {
    mac[0], mac[1], mac[2], mac[3],
    mac[4], mac[5], 0x6b, 0xa2,
    0x50, 0x01, 0x6b, 0x00,
    0x00, 0x00, mac[4], mac[5],
  };
  bytes[6] = (bytes[6] & 0x0F) | 0x50;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  return sentero_uuid_from_bytes(bytes);
}

inline std::string sentero_default_device_id() {
  std::string stored = sentero_nvs_get_string("device_uuid", "");
  str_trim(stored);
  if (stored.length() == 36) return stored;

  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  std::string uuid = sentero_uuid_from_bytes(bytes);

  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
    sentero_nvs_put_string(handle, "device_uuid", uuid.c_str());
    nvs_commit(handle);
    nvs_close(handle);
    return uuid;
  }

  return sentero_mac_fallback_uuid();
}

inline bool sentero_is_missing_device_id(const std::string &device_id) {
  return device_id.length() == 0;
}

class SenteroDiscovery {
 public:
  void loop() {
    if (!esphome::wifi::global_wifi_component->is_connected()) return;
    if (provisioned_()) return;

    const uint32_t now = millis();
    if (now - last_broadcast_ms_ < 2000) return;
    last_broadcast_ms_ = now;

    const std::string payload = payload_();
    int sock = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) return;

    int broadcast_enable = 1;
    ::setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &broadcast_enable, sizeof(broadcast_enable));

    sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(37020);
    addr.sin_addr.s_addr = inet_addr("255.255.255.255");

    ::sendto(sock, payload.c_str(), payload.size(), 0, reinterpret_cast<sockaddr *>(&addr), sizeof(addr));
    ::close(sock);
  }

 private:
  uint32_t last_broadcast_ms_{0};

  std::string payload_() {
    std::string device_id = sentero_default_device_id();

    char payload[360];
    snprintf(payload, sizeof(payload),
             "{\"type\":\"sentero-discovery\","
             "\"protocol\":1,"
             "\"device_id\":\"%s\","
             "\"model\":\"%s\","
             "\"firmware\":\"%s\","
             "\"sensor_type\":\"%s\","
             "\"http_port\":80,"
             "\"capabilities\":["
             "\"presence\","
             "\"motion\","
             "\"fall_detection\","
             "\"signal_quality\"]}",
             device_id.c_str(), SENTERO_DEVICE_MODEL, SENTERO_FIRMWARE_VERSION,
             SENTERO_SENSOR_TYPE);

    return std::string(payload);
  }

  bool provisioned_() {
    return sentero_nvs_get_bool("provisioned", false);
  }
};

class SenteroProvisioning : public AsyncWebHandler {
 public:
  void loop() {
    register_handler_once_();
    mqtt_loop_();

    if (restart_at_ms_ != 0 && millis() >= restart_at_ms_) {
      esp_restart();
    }
  }

  // AsyncWebHandler overrides: /api/provision wird auf demselben
  // web_server_base-Server registriert wie der Captive Portal (Port 80),
  // statt einen eigenen httpd-Server zu starten (der mit dem Captive-
  // Portal-Server um Port 80 konkurrieren wuerde).
  bool canHandle(AsyncWebServerRequest *request) const override {
#ifdef USE_ESP32
    char url_buf[AsyncWebServerRequest::URL_BUF_SIZE];
    StringRef url = request->url_to(url_buf);
#else
    const auto &url = request->url();
#endif
    return request->method() == HTTP_POST && url == ESPHOME_F("/api/provision");
  }

  // Wichtig: ESPHomes web_server_idf ruft AsyncWebHandler::handleBody()
  // fuer POST-Requests NIE auf (das existiert nur aus API-Kompatibilitaet
  // zur Arduino-Variante). Fuer alles ausser Content-Type
  // application/x-www-form-urlencoded / multipart/form-data liefert es die
  // Anfrage wie ein GET aus, ohne den Body zu lesen. Fuer unser JSON lesen
  // wir den Body deshalb hier selbst ueber den rohen httpd_req_t*.
  void handleRequest(AsyncWebServerRequest *request) override {
    process_provision_(request);
  }

  void factory_reset() {
    Config config;
    const bool has_config = load_config_(config);
    factory_reset_(has_config ? &config : nullptr);
  }

 private:
  static constexpr size_t MAX_BODY_SIZE = 4096;
  bool registered_{false};
  esp_mqtt_client_handle_t mqtt_{nullptr};
  bool mqtt_connected_{false};
  bool mqtt_configured_{false};
  uint32_t last_state_publish_ms_{0};
  uint32_t last_availability_publish_ms_{0};
  uint32_t restart_at_ms_{0};
  std::string last_state_signature_;
  std::string mqtt_uri_;
  std::string mqtt_client_id_;
  std::string mqtt_username_;
  std::string mqtt_password_;
  std::string mqtt_lwt_topic_;
  std::string mqtt_lwt_payload_;
  std::string mqtt_command_topic_;

  struct Config {
    std::string device_id;
    std::string friendly_name;
    std::string room_id;
    std::string mqtt_host;
    uint16_t mqtt_port{1883};
    std::string mqtt_username;
    std::string mqtt_password;
    std::string topic_prefix{"sentero"};
  };

  static constexpr uint32_t STATE_CHANGE_MIN_INTERVAL_MS = 1000;
  static constexpr uint32_t STATE_HEARTBEAT_INTERVAL_MS = 30 * 1000;
  static constexpr uint32_t AVAILABILITY_INTERVAL_MS = 60 * 1000;

  void register_handler_once_() {
    if (registered_) return;
    auto *base = esphome::web_server_base::global_web_server_base;
    if (base == nullptr) return;

    base->add_handler_without_auth(this);
    registered_ = true;
    ESP_LOGI(SENTERO_LOG_TAG, "Sentero provisioning API registriert (/api/provision)");
  }

  void process_provision_(AsyncWebServerRequest *request) {
    // Ein bereits provisioniertes Geraet darf nicht von einem zweiten Client
    // umprovisioniert werden. Das geht erst wieder nach einem Factory Reset
    // (z.B. per MQTT-Kommando {"command":"factory_reset"}), das den
    // "provisioned"-Flag im NVS loescht.
    if (sentero_nvs_get_bool("provisioned", false)) {
      send_json_(request, 409, "{\"success\":false,\"error\":\"already_provisioned\"}");
      return;
    }

    httpd_req_t *req = *request;
    if (req->content_len == 0 || req->content_len > MAX_BODY_SIZE) {
      send_json_(request, 400, "{\"success\":false,\"error\":\"body_too_large\"}");
      return;
    }

    std::string body;
    body.resize(req->content_len);
    size_t received_total = 0;
    while (received_total < req->content_len) {
      const int received = httpd_req_recv(req, &body[received_total], req->content_len - received_total);
      if (received <= 0) {
        send_json_(request, 400, "{\"success\":false,\"error\":\"request_read_failed\"}");
        return;
      }
      received_total += static_cast<size_t>(received);
    }

    StaticJsonDocument<2048> doc;
    DeserializationError error = deserializeJson(doc, body);
    if (error) {
      send_json_(request, 400, "{\"success\":false,\"error\":\"invalid_json\"}");
      return;
    }

    const int protocol = doc["protocol"] | 1;
    if (protocol < 1 || protocol > 2) {
      send_json_(request, 400, "{\"success\":false,\"error\":\"unsupported_protocol\"}");
      return;
    }

    JsonObject mqtt = doc["mqtt"];
    JsonObject device = doc["device"];
    JsonObject wifi = doc["wifi"];
    const char *mqtt_host = mqtt["host"] | "";
    if (strlen(mqtt_host) == 0) mqtt_host = doc["mqtt_host"] | "";
    const char *wifi_ssid = wifi["ssid"] | "";
    if (strlen(wifi_ssid) == 0) wifi_ssid = doc["wifi_ssid"] | "";
    const char *wifi_password = wifi["password"] | "";
    if (strlen(wifi_password) == 0) wifi_password = doc["wifi_password"] | "";
    std::string device_id = device["device_id"] | "";
    if (device_id.length() == 0) device_id = doc["device_id"] | "";
    str_trim(device_id);
    if (sentero_is_missing_device_id(device_id)) device_id = sentero_default_device_id();
    const char *friendly_name = device["friendly_name"] | "";
    if (strlen(friendly_name) == 0) friendly_name = device["display_name"] | "";
    if (strlen(friendly_name) == 0) friendly_name = doc["friendly_name"] | "";
    if (strlen(friendly_name) == 0) friendly_name = doc["display_name"] | "";
    if (strlen(friendly_name) == 0) friendly_name = doc["name"] | "";
    const char *topic_prefix = mqtt["topic_prefix"] | "";
    if (strlen(topic_prefix) == 0) topic_prefix = doc["topic_prefix"] | "sentero";
    const char *mqtt_username = mqtt["username"] | "";
    if (strlen(mqtt_username) == 0) mqtt_username = doc["mqtt_username"] | "";
    const char *mqtt_password = mqtt["password"] | "";
    if (strlen(mqtt_password) == 0) mqtt_password = doc["mqtt_password"] | "";
    const char *room_id = device["room_id"] | "";
    if (strlen(room_id) == 0) room_id = doc["room_id"] | "";
    const char *device_token = device["token"] | "";
    if (strlen(device_token) == 0) device_token = doc["device_token"] | "";
    if (strlen(device_token) == 0) device_token = doc["token"] | "";
    uint16_t mqtt_port = mqtt["port"] | 0;
    if (mqtt_port == 0) mqtt_port = doc["mqtt_port"] | 1883;
    if (strlen(mqtt_host) == 0) {
      send_json_(request, 400, "{\"success\":false,\"error\":\"missing_required_fields\"}");
      return;
    }

    nvs_handle_t prefs;
    if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) != ESP_OK) {
      send_json_(request, 500, "{\"success\":false,\"error\":\"nvs_open_failed\"}");
      return;
    }
    nvs_set_u8(prefs, "provisioned", 1);
    sentero_nvs_put_string(prefs, "wifi_ssid", wifi_ssid);
    sentero_nvs_put_string(prefs, "wifi_pass", wifi_password);
    sentero_nvs_put_string(prefs, "mqtt_host", mqtt_host);
    nvs_set_u16(prefs, "mqtt_port", mqtt_port);
    sentero_nvs_put_string(prefs, "mqtt_user", mqtt_username);
    sentero_nvs_put_string(prefs, "mqtt_pass", mqtt_password);
    sentero_nvs_put_string(prefs, "topic_prefix", topic_prefix);
    sentero_nvs_put_string(prefs, "device_id", device_id.c_str());
    sentero_nvs_put_string(prefs, "friendly", friendly_name);
    sentero_nvs_put_string(prefs, "room_id", room_id);
    sentero_nvs_put_string(prefs, "token", device_token);
    nvs_commit(prefs);
    nvs_close(prefs);

    mqtt_configured_ = false;
    last_state_publish_ms_ = 0;
    last_availability_publish_ms_ = 0;
    last_state_signature_ = "";
    if (mqtt_ != nullptr) {
      esp_mqtt_client_stop(mqtt_);
      esp_mqtt_client_destroy(mqtt_);
      mqtt_ = nullptr;
      mqtt_connected_ = false;
    }

    apply_wifi_config_(wifi_ssid, wifi_password);

    char response[200];
    snprintf(response, sizeof(response),
             "{\"ok\":true,\"success\":true,\"device_id\":\"%s\",\"model\":\"%s\",\"firmware\":\"%s\"}",
             device_id.c_str(), SENTERO_DEVICE_MODEL, SENTERO_FIRMWARE_VERSION);
    send_json_(request, 200, response);
    restart_at_ms_ = millis() + 1500;
  }

  void apply_wifi_config_(const char *ssid, const char *password) {
    if (ssid == nullptr || strlen(ssid) == 0) return;

    wifi_config_t config = {};
    strlcpy(reinterpret_cast<char *>(config.sta.ssid), ssid, sizeof(config.sta.ssid));
    strlcpy(reinterpret_cast<char *>(config.sta.password), password == nullptr ? "" : password, sizeof(config.sta.password));
    config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    esp_wifi_set_storage(WIFI_STORAGE_FLASH);
    if (esp_wifi_set_config(WIFI_IF_STA, &config) == ESP_OK) {
      ESP_LOGI(SENTERO_LOG_TAG, "Provisioning WLAN gespeichert, Neustart folgt");
    } else {
      ESP_LOGW(SENTERO_LOG_TAG, "Provisioning WLAN konnte nicht in ESP-IDF gespeichert werden");
    }
  }

  void send_json_(AsyncWebServerRequest *request, int status, const char *body) {
    request->send(status, ESPHOME_F("application/json"), body);
  }

  void mqtt_loop_() {
    if (!esphome::wifi::global_wifi_component->is_connected()) return;

    Config config;
    if (!load_config_(config)) return;

    if (!mqtt_configured_) {
      start_mqtt_(config);
      mqtt_configured_ = true;
    }

    if (!mqtt_connected_) return;

    const uint32_t now = millis();
    if (now - last_availability_publish_ms_ >= AVAILABILITY_INTERVAL_MS) {
      publish_availability_(config, "online");
      last_availability_publish_ms_ = now;
    }

    const MR60BDA2Snapshot sensor = mr60bda2_get_snapshot();
    const std::string signature = state_signature_(sensor);
    const bool sensor_changed = signature != last_state_signature_;
    if ((sensor_changed && now - last_state_publish_ms_ >= STATE_CHANGE_MIN_INTERVAL_MS) ||
        now - last_state_publish_ms_ >= STATE_HEARTBEAT_INTERVAL_MS) {
      last_state_publish_ms_ = now;
      publish_state_(config);
      last_state_signature_ = signature;
    }
  }

  void start_mqtt_(const Config &config) {
    mqtt_uri_ = "mqtt://" + config.mqtt_host + ":" + std::to_string(config.mqtt_port);
    mqtt_client_id_ = "sentero-" + config.device_id;
    mqtt_username_ = config.mqtt_username;
    mqtt_password_ = config.mqtt_password;
    mqtt_lwt_topic_ = topic_(config, "availability");
    mqtt_lwt_payload_ = availability_payload_(config, "offline");
    mqtt_command_topic_ = topic_(config, "command");
    last_state_publish_ms_ = 0;
    last_availability_publish_ms_ = 0;
    last_state_signature_ = "";

    esp_mqtt_client_config_t mqtt_cfg = {};
    mqtt_cfg.broker.address.uri = mqtt_uri_.c_str();
    mqtt_cfg.credentials.client_id = mqtt_client_id_.c_str();
    if (mqtt_username_.length() > 0) {
      mqtt_cfg.credentials.username = mqtt_username_.c_str();
      mqtt_cfg.credentials.authentication.password = mqtt_password_.c_str();
    }
    mqtt_cfg.session.last_will.topic = mqtt_lwt_topic_.c_str();
    mqtt_cfg.session.last_will.msg = mqtt_lwt_payload_.c_str();
    mqtt_cfg.session.last_will.msg_len = mqtt_lwt_payload_.length();
    mqtt_cfg.session.last_will.qos = 0;
    mqtt_cfg.session.last_will.retain = 1;
    mqtt_cfg.network.reconnect_timeout_ms = 5000;

    mqtt_ = esp_mqtt_client_init(&mqtt_cfg);
    if (mqtt_ == nullptr) {
      ESP_LOGW(SENTERO_LOG_TAG, "MQTT init fehlgeschlagen host=%s port=%u", config.mqtt_host.c_str(), config.mqtt_port);
      return;
    }
    ESP_LOGI(SENTERO_LOG_TAG, "MQTT Verbindung startet host=%s port=%u topic_prefix=%s device_id=%s",
             config.mqtt_host.c_str(), config.mqtt_port, config.topic_prefix.c_str(), config.device_id.c_str());
    esp_mqtt_client_register_event(mqtt_, MQTT_EVENT_ANY, &SenteroProvisioning::mqtt_event_handler_, this);
    esp_mqtt_client_start(mqtt_);
  }

  static void mqtt_event_handler_(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    static_cast<SenteroProvisioning *>(handler_args)->handle_mqtt_event_(event_id, event_data);
  }

  void handle_mqtt_event_(int32_t event_id, void *event_data) {
    esp_mqtt_event_handle_t event = static_cast<esp_mqtt_event_handle_t>(event_data);
    if (event_id == MQTT_EVENT_CONNECTED) {
      ESP_LOGI(SENTERO_LOG_TAG, "MQTT verbunden");
      mqtt_connected_ = true;
      esp_mqtt_client_subscribe(mqtt_, mqtt_command_topic_.c_str(), 0);

      Config config;
      if (load_config_(config)) {
        publish_availability_(config, "online");
        last_availability_publish_ms_ = millis();
        publish_state_(config);
        last_state_publish_ms_ = millis();
        last_state_signature_ = state_signature_(mr60bda2_get_snapshot());
      }
    } else if (event_id == MQTT_EVENT_DISCONNECTED) {
      ESP_LOGW(SENTERO_LOG_TAG, "MQTT getrennt");
      mqtt_connected_ = false;
    } else if (event_id == MQTT_EVENT_ERROR) {
      ESP_LOGW(SENTERO_LOG_TAG, "MQTT Fehler");
    } else if (event_id == MQTT_EVENT_DATA) {
      std::string topic(event->topic, event->topic + event->topic_len);
      std::string payload(event->data, event->data + event->data_len);
      handle_mqtt_message_(topic, payload);
    }
  }

  bool load_config_(Config &config) {
    const bool provisioned = sentero_nvs_get_bool("provisioned", false);
    config.device_id = sentero_nvs_get_string("device_id", "");
    config.friendly_name = sentero_nvs_get_string("friendly", "");
    config.room_id = sentero_nvs_get_string("room_id", "");
    config.mqtt_host = sentero_nvs_get_string("mqtt_host", "");
    config.mqtt_port = sentero_nvs_get_u16("mqtt_port", 1883);
    config.mqtt_username = sentero_nvs_get_string("mqtt_user", "");
    config.mqtt_password = sentero_nvs_get_string("mqtt_pass", "");
    config.topic_prefix = sentero_nvs_get_string("topic_prefix", "sentero");
    str_trim(config.device_id);
    str_trim(config.mqtt_host);
    str_trim(config.topic_prefix);
    while (str_starts_with(config.topic_prefix, "/")) config.topic_prefix.erase(0, 1);
    while (str_ends_with(config.topic_prefix, "/")) config.topic_prefix.erase(config.topic_prefix.length() - 1);
    if (config.topic_prefix.length() == 0) config.topic_prefix = "sentero";
    return provisioned && config.device_id.length() > 0 && config.mqtt_host.length() > 0;
  }

  std::string topic_(const Config &config, const char *suffix) {
    return config.topic_prefix + "/" + config.device_id + "/" + suffix;
  }

  std::string availability_payload_(const Config &config, const char *status) {
    StaticJsonDocument<192> doc;
    doc["device_id"] = config.device_id;
    doc["status"] = status;
    doc["firmware"] = SENTERO_FIRMWARE_VERSION;
    std::string payload;
    serializeJson(doc, payload);
    return payload;
  }

  void publish_availability_(const Config &config, const char *status) {
    const std::string payload = availability_payload_(config, status);
    publish_(topic_(config, "availability"), payload, true);
  }

  void publish_state_(const Config &config) {
    const MR60BDA2Snapshot sensor = mr60bda2_get_snapshot();

    StaticJsonDocument<1536> doc;
    const std::string display_name = config.friendly_name.length() > 0
        ? config.friendly_name
        : std::string("MR60BDA2 Praesenz");
    doc["device_id"] = config.device_id;
    doc["name"] = display_name;
    doc["type"] = SENTERO_SENSOR_TYPE;
    doc["manufacturer"] = SENTERO_MANUFACTURER;
    doc["model"] = SENTERO_DEVICE_MODEL;
    doc["firmware"] = SENTERO_FIRMWARE_VERSION;
    doc["status"] = "online";
    JsonArray capabilities = doc.createNestedArray("capabilities");
    capabilities.add("presence");
    capabilities.add("motion");
    capabilities.add("fall_detection");
    capabilities.add("signal_quality");
    doc["presence"] = sensor.presence;
    doc["motion"] = sensor.motion;
    doc["fall_detected"] = sensor.fall_detected;
    doc["sensor_ready"] = sensor.ready;
    doc["sensor_status"] = sensor.ready ? "OK" : "Warte auf Sensordaten";
    doc["setup_attempts"] = 1;
    doc["read_errors"] = 0;
    doc["last_sensor_update_ms"] = sensor.last_update_ms;
    doc["last_value_change_ms"] = sensor.last_value_change_ms;
    doc["last_frame_ms"] = sensor.last_frame_ms;
    if (sensor.last_frame.length() > 0) doc["last_frame"] = sensor.last_frame;
    doc["power_source"] = "usb";
    doc["signal_quality"] = signal_quality_();
    doc["command_topic"] = topic_(config, "command");
    // MR60BDA2 hat (im Gegensatz zum C1001) keine per-MQTT konfigurierbaren
    // Sensor-Parameter (keine LEDs, keine Install Height etc.) -> bewusst leer.
    doc.createNestedArray("writable_settings");
    if (config.friendly_name.length() > 0) doc["friendly_name"] = config.friendly_name;
    if (config.room_id.length() > 0) {
      doc["room_id"] = config.room_id;
      doc["room_hint"] = config.room_id;
    }

    std::string payload;
    serializeJson(doc, payload);
    publish_(topic_(config, "state"), payload, true);
  }

  std::string state_signature_(const MR60BDA2Snapshot &sensor) {
    char signature[96];
    snprintf(signature, sizeof(signature), "%u|%u|%u|%s|%u|%u",
             sensor.ready ? 1 : 0,
             sensor.presence ? 1 : 0,
             sensor.fall_detected ? 1 : 0,
             sensor.motion,
             sensor.last_value_change_ms,
             sensor.last_frame_ms);
    return std::string(signature);
  }

  void publish_(const std::string &topic, const std::string &payload, bool retain) {
    if (mqtt_ == nullptr || !mqtt_connected_) return;
    const int message_id = esp_mqtt_client_publish(mqtt_, topic.c_str(), payload.c_str(), payload.length(), 0, retain ? 1 : 0);
    ESP_LOGI(SENTERO_LOG_TAG, "MQTT publish topic=%s retain=%d message_id=%d", topic.c_str(), retain ? 1 : 0, message_id);
  }

  int signal_quality_() {
    wifi_ap_record_t ap = {};
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
    const int rssi = ap.rssi;
    if (rssi <= -100) return 0;
    if (rssi >= -50) return 100;
    return 2 * (rssi + 100);
  }

  void publish_command_status_(const Config &config, const char *command, bool ok, const char *message) {
    StaticJsonDocument<256> doc;
    doc["device_id"] = config.device_id;
    doc["status"] = ok ? "command_accepted" : "command_rejected";
    doc["command"] = command == nullptr ? "" : command;
    doc["ok"] = ok;
    doc["message"] = message == nullptr ? "" : message;

    std::string payload;
    serializeJson(doc, payload);
    publish_(topic_(config, "status"), payload, false);
  }

  // MR60BDA2 hat keine per-Software steuerbaren Sensor-Parameter, daher
  // reduziert sich "configure" hier auf Geraete-Metadaten (friendly_name,
  // room_id). Fuer C1001 gab es hier zusaetzlich hp_led/fall_led/install
  // height/fall time/unmanned time/residence time/fall sensitivity.
  bool apply_configure_command_(const Config &config, JsonObjectConst root, const char *command) {
    JsonVariantConst settings_value = root["settings"];
    JsonObjectConst settings = settings_value.is<JsonObjectConst>() ? settings_value.as<JsonObjectConst>() : root;
    JsonVariantConst device_value = root["device"];
    JsonObjectConst device = device_value.is<JsonObjectConst>() ? device_value.as<JsonObjectConst>() : JsonObjectConst();

    const bool has_friendly_name =
        !device["friendly_name"].isNull() || !device["display_name"].isNull() ||
        !settings["friendly_name"].isNull() || !settings["display_name"].isNull() || !settings["name"].isNull() ||
        !root["friendly_name"].isNull() || !root["display_name"].isNull() || !root["name"].isNull();
    const bool has_room_id =
        !device["room_id"].isNull() || !settings["room_id"].isNull() || !settings["room_hint"].isNull() ||
        !root["room_id"].isNull() || !root["room_hint"].isNull();
    std::string friendly_name;
    std::string room_id;

    if (has_friendly_name) {
      friendly_name = device["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = device["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["name"] | "";
      str_trim(friendly_name);
      if (friendly_name.length() == 0 || friendly_name.length() > 64) {
        publish_command_status_(config, command, false, "invalid_friendly_name");
        return true;
      }
    }
    if (has_room_id) {
      room_id = device["room_id"] | "";
      if (room_id.length() == 0) room_id = settings["room_id"] | "";
      if (room_id.length() == 0) room_id = settings["room_hint"] | "";
      if (room_id.length() == 0) room_id = root["room_id"] | "";
      if (room_id.length() == 0) room_id = root["room_hint"] | "";
      str_trim(room_id);
      if (room_id.length() > 64) {
        publish_command_status_(config, command, false, "invalid_room_id");
        return true;
      }
    }

    if (!has_friendly_name && !has_room_id) {
      publish_command_status_(config, command, false, "no_known_settings");
      return true;
    }

    nvs_handle_t prefs;
    if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) != ESP_OK) {
      publish_command_status_(config, command, false, "nvs_open_failed");
      return true;
    }

    if (has_friendly_name) sentero_nvs_put_string(prefs, "friendly", friendly_name.c_str());
    if (has_room_id) sentero_nvs_put_string(prefs, "room_id", room_id.c_str());
    const esp_err_t commit_result = nvs_commit(prefs);
    nvs_close(prefs);
    if (commit_result != ESP_OK) {
      publish_command_status_(config, command, false, "nvs_commit_failed");
      return true;
    }

    publish_command_status_(config, command, true, "configuration_applied");
    Config updated_config;
    if (load_config_(updated_config)) {
      publish_state_(updated_config);
      last_state_publish_ms_ = millis();
      last_state_signature_ = state_signature_(mr60bda2_get_snapshot());
    }
    return true;
  }

  void handle_mqtt_message_(const std::string &topic, const std::string &payload) {
    Config config;
    if (!load_config_(config)) return;
    if (std::string(topic.c_str()) != topic_(config, "command")) return;

    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, payload)) {
      publish_command_status_(config, "", false, "invalid_json");
      return;
    }

    JsonObjectConst root = doc.as<JsonObjectConst>();
    std::string command = root["command"] | "";
    str_trim(command);
    str_to_lower(command);
    str_replace_all(command, "-", "_");
    const char *command_c = command.c_str();

    if (command.length() == 0) {
      publish_command_status_(config, "", false, "missing_command");
      return;
    }
    if (command == "factory_reset" || command == "factory_resetting") {
      factory_reset_(&config);
      return;
    }
    // Hinweis: MR60BDA2 hat (anders als C1001) keine per-Software steuerbaren
    // Sensor-Parameter (kein Reset-Kommando, keine LEDs, keine Install
    // Height etc.) -> diese Commands entfallen ersatzlos. Uebrig bleibt nur
    // die Geraete-Konfiguration (friendly_name/room_id).
    if (command == "configure" || command == "set_config") {
      apply_configure_command_(config, root, command_c);
      return;
    }

    publish_command_status_(config, command_c, false, "unsupported_command");
  }

  void factory_reset_(const Config *config) {
    ESP_LOGW(SENTERO_LOG_TAG, "Factory Reset: Sentero Provisioning-Daten werden geloescht");
    std::string preserved_uuid = sentero_nvs_get_string("device_uuid", "");
    str_trim(preserved_uuid);
    if (preserved_uuid.length() != 36 && config != nullptr && config->device_id.length() == 36) {
      preserved_uuid = config->device_id;
    }

    if (config != nullptr) {
      StaticJsonDocument<160> status;
      status["device_id"] = config->device_id;
      status["status"] = "factory_resetting";
      std::string body;
      serializeJson(status, body);
      publish_(topic_(*config, "status"), body, false);
      publish_availability_(*config, "offline");
      delay(100);
    }

    nvs_handle_t prefs;
    if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) == ESP_OK) {
      nvs_erase_all(prefs);
      if (preserved_uuid.length() == 36) {
        sentero_nvs_put_string(prefs, "device_uuid", preserved_uuid.c_str());
      }
      nvs_commit(prefs);
      nvs_close(prefs);
    }
    esp_wifi_restore();
    restart_at_ms_ = millis() + 500;
  }

};

static SenteroDiscovery sentero_discovery;
static SenteroProvisioning sentero_provisioning;

inline void sentero_discovery_loop() {
  sentero_discovery.loop();
}

inline void sentero_provisioning_loop() {
  sentero_provisioning.loop();
}

inline void sentero_factory_reset() {
  sentero_provisioning.factory_reset();
}
