#pragma once

#include "c1001_bridge.h"
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
static constexpr const char *SENTERO_DEVICE_MODEL = "C1001";
static constexpr const char *SENTERO_SENSOR_TYPE = "presence_radar";
static constexpr const char *SENTERO_FIRMWARE_VERSION = "1.0.1";

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

inline String sentero_nvs_get_string(const char *key, const char *fallback = "") {
  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return String(fallback);
  size_t len = 0;
  if (nvs_get_str(handle, key, nullptr, &len) != ESP_OK || len == 0) {
    nvs_close(handle);
    return String(fallback);
  }
  std::string value;
  value.resize(len);
  if (nvs_get_str(handle, key, &value[0], &len) != ESP_OK) {
    nvs_close(handle);
    return String(fallback);
  }
  nvs_close(handle);
  if (!value.empty() && value.back() == '\0') value.pop_back();
  return String(value.c_str());
}

inline void sentero_nvs_put_string(nvs_handle_t handle, const char *key, const char *value) {
  nvs_set_str(handle, key, value == nullptr ? "" : value);
}

inline String sentero_uuid_from_bytes(const uint8_t bytes[16]) {
  char uuid[37];
  snprintf(uuid, sizeof(uuid),
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3],
           bytes[4], bytes[5],
           bytes[6], bytes[7],
           bytes[8], bytes[9],
           bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]);
  return String(uuid);
}

inline String sentero_mac_fallback_uuid() {
  uint8_t mac[6];
  get_mac_address_raw(mac);

  uint8_t bytes[16] = {
    mac[0], mac[1], mac[2], mac[3],
    mac[4], mac[5], 0xc1, 0x01,
    0x50, 0x01, 0xc1, 0x00,
    0x00, 0x00, mac[4], mac[5],
  };
  bytes[6] = (bytes[6] & 0x0F) | 0x50;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  return sentero_uuid_from_bytes(bytes);
}

inline String sentero_default_device_id() {
  String stored = sentero_nvs_get_string("device_uuid", "");
  stored.trim();
  if (stored.length() == 36) return stored;

  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  String uuid = sentero_uuid_from_bytes(bytes);

  nvs_handle_t handle;
  if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &handle) == ESP_OK) {
    sentero_nvs_put_string(handle, "device_uuid", uuid.c_str());
    nvs_commit(handle);
    nvs_close(handle);
    return uuid;
  }

  return sentero_mac_fallback_uuid();
}

inline bool sentero_is_missing_device_id(const String &device_id) {
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
    String device_id = sentero_default_device_id();

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
      ESP.restart();
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
  String last_state_signature_;
  String mqtt_uri_;
  String mqtt_client_id_;
  String mqtt_username_;
  String mqtt_password_;
  String mqtt_lwt_topic_;
  String mqtt_lwt_payload_;
  String mqtt_command_topic_;

  struct Config {
    String device_id;
    String friendly_name;
    String room_id;
    bool hp_led{false};
    bool fall_led{false};
    String mqtt_host;
    uint16_t mqtt_port{1883};
    String mqtt_username;
    String mqtt_password;
    String topic_prefix{"sentero"};
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
    String device_id = device["device_id"] | "";
    if (device_id.length() == 0) device_id = doc["device_id"] | "";
    device_id.trim();
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

    apply_desired_led_state_(config);

    const uint32_t now = millis();
    if (now - last_availability_publish_ms_ >= AVAILABILITY_INTERVAL_MS) {
      publish_availability_(config, "online");
      last_availability_publish_ms_ = now;
    }

    const C1001Snapshot sensor = c1001_get_snapshot();
    const String signature = state_signature_(sensor);
    const bool sensor_changed = signature != last_state_signature_;
    if ((sensor_changed && now - last_state_publish_ms_ >= STATE_CHANGE_MIN_INTERVAL_MS) ||
        now - last_state_publish_ms_ >= STATE_HEARTBEAT_INTERVAL_MS) {
      last_state_publish_ms_ = now;
      publish_state_(config);
      last_state_signature_ = signature;
    }
  }

  void start_mqtt_(const Config &config) {
    mqtt_uri_ = "mqtt://" + config.mqtt_host + ":" + String(config.mqtt_port);
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
        last_state_signature_ = state_signature_(c1001_get_snapshot());
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
    config.hp_led = sentero_nvs_get_bool("hp_led", false);
    config.fall_led = sentero_nvs_get_bool("fall_led", false);
    config.mqtt_host = sentero_nvs_get_string("mqtt_host", "");
    config.mqtt_port = sentero_nvs_get_u16("mqtt_port", 1883);
    config.mqtt_username = sentero_nvs_get_string("mqtt_user", "");
    config.mqtt_password = sentero_nvs_get_string("mqtt_pass", "");
    config.topic_prefix = sentero_nvs_get_string("topic_prefix", "sentero");
    config.device_id.trim();
    config.mqtt_host.trim();
    config.topic_prefix.trim();
    while (config.topic_prefix.startsWith("/")) config.topic_prefix.remove(0, 1);
    while (config.topic_prefix.endsWith("/")) config.topic_prefix.remove(config.topic_prefix.length() - 1);
    if (config.topic_prefix.length() == 0) config.topic_prefix = "sentero";
    return provisioned && config.device_id.length() > 0 && config.mqtt_host.length() > 0;
  }

  bool store_led_preference_(const char *key, bool value) {
    nvs_handle_t prefs;
    if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) != ESP_OK) return false;
    const esp_err_t set_result = nvs_set_u8(prefs, key, value ? 1 : 0);
    const esp_err_t commit_result = nvs_commit(prefs);
    nvs_close(prefs);
    return set_result == ESP_OK && commit_result == ESP_OK;
  }

  void apply_desired_led_state_(const Config &config) {
    const C1001Snapshot sensor = c1001_get_snapshot();
    if (!sensor.ready) return;
    bool changed = false;
    if (!sensor.hp_led_known || sensor.hp_led != config.hp_led) {
      changed = c1001_set_hp_led(config.hp_led) || changed;
    }
    if (!sensor.fall_led_known || sensor.fall_led != config.fall_led) {
      changed = c1001_set_fall_led(config.fall_led) || changed;
    }
    if (changed) {
      last_state_publish_ms_ = 0;
      last_state_signature_ = "";
    }
  }

  String topic_(const Config &config, const char *suffix) {
    return config.topic_prefix + "/" + config.device_id + "/" + suffix;
  }

  String availability_payload_(const Config &config, const char *status) {
    StaticJsonDocument<192> doc;
    doc["device_id"] = config.device_id;
    doc["status"] = status;
    doc["firmware"] = SENTERO_FIRMWARE_VERSION;
    String payload;
    serializeJson(doc, payload);
    return payload;
  }

  void publish_availability_(const Config &config, const char *status) {
    const String payload = availability_payload_(config, status);
    publish_(topic_(config, "availability"), payload, true);
  }

  void publish_state_(const Config &config) {
    const C1001Snapshot sensor = c1001_get_snapshot();

    StaticJsonDocument<1792> doc;
    const String display_name = config.friendly_name.length() > 0
        ? config.friendly_name
        : String("C1001 Praesenz");
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
    doc["fall_detected"] = sensor.fall_detected;
    doc["motion"] = sensor.motion;
    doc["presence_raw"] = sensor.presence_raw;
    doc["motion_raw"] = sensor.motion_raw;
    doc["fall_raw"] = sensor.fall_raw;
    doc["moving_range"] = sensor.moving_range;
    doc["work_mode"] = sensor.work_mode;
    doc["sensor_ready"] = sensor.ready;
    doc["sensor_status"] = sensor.status;
    doc["setup_attempts"] = sensor.setup_attempts;
    doc["poll_count"] = sensor.poll_count;
    doc["poll_ok_count"] = sensor.poll_ok_count;
    doc["poll_error_count"] = sensor.poll_error_count;
    doc["read_errors"] = sensor.read_errors;
    doc["stuck_active_resets"] = sensor.stuck_active_resets;
    doc["stuck_presence_resets"] = sensor.stuck_presence_resets;
    doc["stuck_inactive_resets"] = sensor.stuck_inactive_resets;
    doc["last_sensor_update_ms"] = sensor.last_update_ms;
    doc["last_value_change_ms"] = sensor.last_value_change_ms;
    doc["last_poll_ms"] = sensor.last_poll_ms;
    doc["last_poll_ok_ms"] = sensor.last_poll_ok_ms;
    doc["last_frame_ms"] = sensor.last_frame_ms;
    doc["power_source"] = "usb";
    doc["signal_quality"] = signal_quality_();
    JsonObject led_status = doc.createNestedObject("led_status");
    if (sensor.hp_led_known) led_status["hp_led"] = sensor.hp_led;
    else led_status["hp_led"] = nullptr;
    if (sensor.fall_led_known) led_status["fall_led"] = sensor.fall_led;
    else led_status["fall_led"] = nullptr;
    if (sensor.hp_led_known && sensor.fall_led_known) led_status["all_on"] = sensor.hp_led && sensor.fall_led;
    else led_status["all_on"] = nullptr;
    if (sensor.hp_led_known || sensor.fall_led_known) led_status["any_on"] = (sensor.hp_led_known && sensor.hp_led) || (sensor.fall_led_known && sensor.fall_led);
    else led_status["any_on"] = nullptr;
    doc["command_topic"] = topic_(config, "command");
    JsonArray writable_settings = doc.createNestedArray("writable_settings");
    writable_settings.add("hp_led");
    writable_settings.add("fall_led");
    writable_settings.add("install_height");
    writable_settings.add("fall_time");
    writable_settings.add("unmanned_time");
    writable_settings.add("residence_time");
    writable_settings.add("fall_sensitivity");
    if (config.friendly_name.length() > 0) doc["friendly_name"] = config.friendly_name;
    if (config.room_id.length() > 0) {
      doc["room_id"] = config.room_id;
      doc["room_hint"] = config.room_id;
    }

    String payload;
    serializeJson(doc, payload);
    publish_(topic_(config, "state"), payload, true);
  }

  String state_signature_(const C1001Snapshot &sensor) {
    char signature[180];
    snprintf(signature, sizeof(signature), "%u|%u|%s|%u|%u|%u|%u|%u|%u|%s|%u|%u|%u|%u|%u|%u|%u|%u",
             sensor.ready ? 1 : 0,
             sensor.presence ? 1 : 0,
             sensor.motion == nullptr ? "" : sensor.motion,
             sensor.presence_raw,
             sensor.motion_raw,
             sensor.fall_raw,
             sensor.moving_range,
             sensor.work_mode,
             sensor.fall_detected ? 1 : 0,
             sensor.status == nullptr ? "" : sensor.status,
             sensor.poll_count,
             sensor.read_errors,
             sensor.stuck_active_resets,
             sensor.stuck_presence_resets,
             sensor.stuck_inactive_resets,
             sensor.last_value_change_ms,
             sensor.hp_led_known ? (sensor.hp_led ? 1 : 0) : 2,
             sensor.fall_led_known ? (sensor.fall_led ? 1 : 0) : 2);
    return String(signature);
  }

  void publish_(const String &topic, const String &payload, bool retain) {
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
    StaticJsonDocument<384> doc;
    const C1001Snapshot sensor = c1001_get_snapshot();
    doc["device_id"] = config.device_id;
    doc["status"] = ok ? "command_accepted" : "command_rejected";
    doc["command"] = command == nullptr ? "" : command;
    doc["ok"] = ok;
    doc["message"] = message == nullptr ? "" : message;
    JsonObject led_status = doc.createNestedObject("led_status");
    if (sensor.hp_led_known) led_status["hp_led"] = sensor.hp_led;
    else led_status["hp_led"] = nullptr;
    if (sensor.fall_led_known) led_status["fall_led"] = sensor.fall_led;
    else led_status["fall_led"] = nullptr;
    if (sensor.hp_led_known && sensor.fall_led_known) led_status["all_on"] = sensor.hp_led && sensor.fall_led;
    else led_status["all_on"] = nullptr;
    if (sensor.hp_led_known || sensor.fall_led_known) led_status["any_on"] = (sensor.hp_led_known && sensor.hp_led) || (sensor.fall_led_known && sensor.fall_led);
    else led_status["any_on"] = nullptr;

    String payload;
    serializeJson(doc, payload);
    publish_(topic_(config, "status"), payload, false);
  }

  bool read_bool_(JsonVariantConst value, bool &out) {
    if (value.isNull()) return false;
    if (value.is<bool>()) {
      out = value.as<bool>();
      return true;
    }
    if (value.is<int>()) {
      out = value.as<int>() != 0;
      return true;
    }
    if (value.is<const char *>()) {
      String text = value.as<const char *>();
      text.trim();
      text.toLowerCase();
      if (text == "true" || text == "on" || text == "1" || text == "yes") {
        out = true;
        return true;
      }
      if (text == "false" || text == "off" || text == "0" || text == "no") {
        out = false;
        return true;
      }
    }
    return false;
  }

  bool read_number_(JsonVariantConst value, float &out) {
    if (value.isNull()) return false;
    if (value.is<float>() || value.is<double>() || value.is<int>() || value.is<long>() ||
        value.is<unsigned int>() || value.is<unsigned long>()) {
      out = value.as<float>();
      return true;
    }
    return false;
  }

  bool bool_arg_(JsonObjectConst root, bool &out) {
    return read_bool_(root["enabled"], out) ||
           read_bool_(root["value"], out) ||
           read_bool_(root["state"], out) ||
           read_bool_(root["on"], out);
  }

  bool number_arg_(JsonObjectConst root, const char *primary_key, float &out) {
    if (primary_key != nullptr && read_number_(root[primary_key], out)) return true;
    return read_number_(root["value"], out);
  }

  bool apply_bool_command_(const Config &config, JsonObjectConst root, const char *command,
                           bool (*setter)(bool), const char *preference_key = nullptr) {
    bool enabled = false;
    if (!bool_arg_(root, enabled)) {
      publish_command_status_(config, command, false, "missing_or_invalid_boolean");
      return true;
    }

    if (!setter(enabled)) {
      publish_command_status_(config, command, false, "sensor_command_failed");
      return true;
    }
    if (preference_key != nullptr && !store_led_preference_(preference_key, enabled)) {
      publish_command_status_(config, command, false, "nvs_commit_failed");
      return true;
    }
    publish_command_status_(config, command, true, enabled ? "enabled" : "disabled");
    Config updated_config;
    publish_state_(load_config_(updated_config) ? updated_config : config);
    last_state_publish_ms_ = millis();
    last_state_signature_ = state_signature_(c1001_get_snapshot());
    return true;
  }

  bool apply_number_command_(const Config &config, JsonObjectConst root, const char *command,
                             const char *primary_key, float min_value, float max_value,
                             void (*setter)(float)) {
    float value = 0;
    if (!number_arg_(root, primary_key, value)) {
      publish_command_status_(config, command, false, "missing_or_invalid_number");
      return true;
    }
    if (value < min_value || value > max_value) {
      publish_command_status_(config, command, false, "value_out_of_range");
      return true;
    }

    setter(value);
    publish_command_status_(config, command, true, "applied");
    return true;
  }

  bool apply_configure_command_(const Config &config, JsonObjectConst root, const char *command) {
    JsonVariantConst settings_value = root["settings"];
    JsonObjectConst settings = settings_value.is<JsonObjectConst>() ? settings_value.as<JsonObjectConst>() : root;
    JsonVariantConst device_value = root["device"];
    JsonObjectConst device = device_value.is<JsonObjectConst>() ? device_value.as<JsonObjectConst>() : JsonObjectConst();

    bool hp_led = false;
    bool fall_led = false;
    float install_height = 0;
    float fall_time = 0;
    float unmanned_time = 0;
    float residence_time = 0;
    float fall_sensitivity = 0;
    const bool has_hp_led = read_bool_(settings["hp_led"], hp_led);
    const bool has_fall_led = read_bool_(settings["fall_led"], fall_led);
    const bool has_install_height = read_number_(settings["install_height"], install_height);
    const bool has_fall_time = read_number_(settings["fall_time"], fall_time);
    const bool has_unmanned_time = read_number_(settings["unmanned_time"], unmanned_time);
    const bool has_residence_time = read_number_(settings["residence_time"], residence_time);
    const bool has_fall_sensitivity = read_number_(settings["fall_sensitivity"], fall_sensitivity);
    const bool has_friendly_name =
        !device["friendly_name"].isNull() || !device["display_name"].isNull() ||
        !settings["friendly_name"].isNull() || !settings["display_name"].isNull() || !settings["name"].isNull() ||
        !root["friendly_name"].isNull() || !root["display_name"].isNull() || !root["name"].isNull();
    const bool has_room_id =
        !device["room_id"].isNull() || !settings["room_id"].isNull() || !settings["room_hint"].isNull() ||
        !root["room_id"].isNull() || !root["room_hint"].isNull();
    String friendly_name;
    String room_id;

    if (has_friendly_name) {
      friendly_name = device["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = device["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = settings["name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["friendly_name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["display_name"] | "";
      if (friendly_name.length() == 0) friendly_name = root["name"] | "";
      friendly_name.trim();
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
      room_id.trim();
      if (room_id.length() > 64) {
        publish_command_status_(config, command, false, "invalid_room_id");
        return true;
      }
    }

    if (!settings["hp_led"].isNull() && !has_hp_led) {
      publish_command_status_(config, command, false, "invalid_hp_led");
      return true;
    }
    if (!settings["fall_led"].isNull() && !has_fall_led) {
      publish_command_status_(config, command, false, "invalid_fall_led");
      return true;
    }
    if (has_install_height && (install_height < 100 || install_height > 400)) {
      publish_command_status_(config, command, false, "install_height_out_of_range");
      return true;
    }
    if (has_fall_time && (fall_time < 0 || fall_time > 60)) {
      publish_command_status_(config, command, false, "fall_time_out_of_range");
      return true;
    }
    if (has_unmanned_time && (unmanned_time < 0 || unmanned_time > 60)) {
      publish_command_status_(config, command, false, "unmanned_time_out_of_range");
      return true;
    }
    if (has_residence_time && (residence_time < 0 || residence_time > 3600)) {
      publish_command_status_(config, command, false, "residence_time_out_of_range");
      return true;
    }
    if (has_fall_sensitivity && (fall_sensitivity < 0 || fall_sensitivity > 3)) {
      publish_command_status_(config, command, false, "fall_sensitivity_out_of_range");
      return true;
    }
    if (!has_hp_led && !has_fall_led && !has_install_height && !has_fall_time &&
        !has_unmanned_time && !has_residence_time && !has_fall_sensitivity &&
        !has_friendly_name && !has_room_id) {
      publish_command_status_(config, command, false, "no_known_settings");
      return true;
    }

    nvs_handle_t prefs;
    bool prefs_open = false;
    if (has_friendly_name || has_room_id || has_hp_led || has_fall_led) {
      if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) != ESP_OK) {
        publish_command_status_(config, command, false, "nvs_open_failed");
        return true;
      }
      prefs_open = true;
    }

    if (has_hp_led && !c1001_set_hp_led(hp_led)) {
      if (prefs_open) nvs_close(prefs);
      publish_command_status_(config, command, false, "hp_led_command_failed");
      return true;
    }
    if (has_fall_led && !c1001_set_fall_led(fall_led)) {
      if (prefs_open) nvs_close(prefs);
      publish_command_status_(config, command, false, "fall_led_command_failed");
      return true;
    }
    if (has_install_height) c1001_set_install_height(install_height);
    if (has_fall_time) c1001_set_fall_time(fall_time);
    if (has_unmanned_time) c1001_set_unmanned_time(unmanned_time);
    if (has_residence_time) c1001_set_residence_time(residence_time);
    if (has_fall_sensitivity) c1001_set_fall_sensitivity(fall_sensitivity);
    if (has_hp_led) nvs_set_u8(prefs, "hp_led", hp_led ? 1 : 0);
    if (has_fall_led) nvs_set_u8(prefs, "fall_led", fall_led ? 1 : 0);
    if (has_friendly_name) sentero_nvs_put_string(prefs, "friendly", friendly_name.c_str());
    if (has_room_id) sentero_nvs_put_string(prefs, "room_id", room_id.c_str());
    if (prefs_open) {
      esp_err_t commit_result = nvs_commit(prefs);
      nvs_close(prefs);
      if (commit_result != ESP_OK) {
        publish_command_status_(config, command, false, "nvs_commit_failed");
        return true;
      }
    }

    publish_command_status_(config, command, true, "configuration_applied");
    if (has_hp_led || has_fall_led || has_friendly_name || has_room_id) {
      Config updated_config;
      if (load_config_(updated_config)) {
        publish_state_(updated_config);
        last_state_publish_ms_ = millis();
        last_state_signature_ = state_signature_(c1001_get_snapshot());
      }
    }
    return true;
  }

  void handle_mqtt_message_(const std::string &topic, const std::string &payload) {
    Config config;
    if (!load_config_(config)) return;
    if (String(topic.c_str()) != topic_(config, "command")) return;

    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, payload)) {
      publish_command_status_(config, "", false, "invalid_json");
      return;
    }

    JsonObjectConst root = doc.as<JsonObjectConst>();
    String command = root["command"] | "";
    command.trim();
    command.toLowerCase();
    command.replace("-", "_");
    const char *command_c = command.c_str();

    if (command.length() == 0) {
      publish_command_status_(config, "", false, "missing_command");
      return;
    }
    if (command == "factory_reset" || command == "factory_resetting") {
      factory_reset_(&config);
      return;
    }
    if (command == "reset_sensor" || command == "sensor_restart" || command == "restart_sensor") {
      c1001_reset_sensor();
      publish_command_status_(config, command_c, true, "sensor_reset_requested");
      return;
    }
    if (command == "set_hp_led" || command == "hp_led") {
      apply_bool_command_(config, root, command_c, c1001_set_hp_led, "hp_led");
      return;
    }
    if (command == "set_fall_led" || command == "fall_led") {
      apply_bool_command_(config, root, command_c, c1001_set_fall_led, "fall_led");
      return;
    }
    if (command == "set_install_height" || command == "install_height") {
      apply_number_command_(config, root, command_c, "centimeters", 100, 400, c1001_set_install_height);
      return;
    }
    if (command == "set_fall_time" || command == "fall_time") {
      apply_number_command_(config, root, command_c, "seconds", 0, 60, c1001_set_fall_time);
      return;
    }
    if (command == "set_unmanned_time" || command == "unmanned_time" || command == "absence_time") {
      apply_number_command_(config, root, command_c, "seconds", 0, 60, c1001_set_unmanned_time);
      return;
    }
    if (command == "set_residence_time" || command == "residence_time") {
      apply_number_command_(config, root, command_c, "seconds", 0, 3600, c1001_set_residence_time);
      return;
    }
    if (command == "set_fall_sensitivity" || command == "fall_sensitivity") {
      apply_number_command_(config, root, command_c, "sensitivity", 0, 3, c1001_set_fall_sensitivity);
      return;
    }
    if (command == "configure" || command == "set_config") {
      apply_configure_command_(config, root, command_c);
      return;
    }

    publish_command_status_(config, command_c, false, "unsupported_command");
  }

  void factory_reset_(const Config *config) {
    ESP_LOGW(SENTERO_LOG_TAG, "Factory Reset: Sentero Provisioning-Daten werden geloescht");
    String preserved_uuid = sentero_nvs_get_string("device_uuid", "");
    preserved_uuid.trim();
    if (preserved_uuid.length() != 36 && config != nullptr && config->device_id.length() == 36) {
      preserved_uuid = config->device_id;
    }

    if (config != nullptr) {
      StaticJsonDocument<160> status;
      status["device_id"] = config->device_id;
      status["status"] = "factory_resetting";
      String body;
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
