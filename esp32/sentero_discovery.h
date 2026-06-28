#pragma once

#include "c1001_bridge.h"
#include "esphome.h"
#include "esphome/components/wifi/wifi_component.h"
#include <ArduinoJson.h>
#include "esp_http_server.h"
#include "esp_log.h"
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
static constexpr const char *SENTERO_FIRMWARE_VERSION = "1.0.0";

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

inline String sentero_default_device_id() {
  uint8_t mac[6];
  get_mac_address_raw(mac);

  char device_id[24];
  snprintf(device_id, sizeof(device_id), "c1001-%02x%02x%02x%02x",
           mac[2], mac[3], mac[4], mac[5]);
  return String(device_id);
}

inline bool sentero_is_placeholder_device_id(const String &device_id) {
  return device_id.length() == 0 || device_id == "c1001-a1b2c3d4";
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

class SenteroProvisioning {
 public:
  void loop() {
    start_http_once_();
    mqtt_loop_();

    if (restart_at_ms_ != 0 && millis() >= restart_at_ms_) {
      ESP.restart();
    }
  }

  void factory_reset() {
    Config config;
    const bool has_config = load_config_(config);
    factory_reset_(has_config ? &config : nullptr);
  }

 private:
  httpd_handle_t server_{nullptr};
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
    String mqtt_host;
    uint16_t mqtt_port{1883};
    String mqtt_username;
    String mqtt_password;
    String topic_prefix{"sentero"};
  };

  static constexpr uint32_t STATE_CHANGE_MIN_INTERVAL_MS = 1000;
  static constexpr uint32_t STATE_HEARTBEAT_INTERVAL_MS = 5 * 60 * 1000;
  static constexpr uint32_t AVAILABILITY_INTERVAL_MS = 60 * 1000;

  void start_http_once_() {
    if (server_ != nullptr) return;
    if (sentero_nvs_get_bool("provisioned", false)) return;
    if (!esphome::wifi::global_wifi_component->is_connected()) return;

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.max_uri_handlers = 4;
    if (httpd_start(&server_, &config) != ESP_OK) {
      server_ = nullptr;
      return;
    }

    httpd_uri_t provision_uri;
    memset(&provision_uri, 0, sizeof(provision_uri));
    provision_uri.uri = "/api/provision";
    provision_uri.method = HTTP_POST;
    provision_uri.handler = &SenteroProvisioning::provision_handler_;
    provision_uri.user_ctx = this;
    httpd_register_uri_handler(server_, &provision_uri);
  }

  static esp_err_t provision_handler_(httpd_req_t *req) {
    return static_cast<SenteroProvisioning *>(req->user_ctx)->handle_provision_(req);
  }

  esp_err_t handle_provision_(httpd_req_t *req) {
    std::string body;
    body.resize(req->content_len);
    int received_total = 0;
    while (received_total < req->content_len) {
      const int received = httpd_req_recv(req, &body[received_total], req->content_len - received_total);
      if (received <= 0) {
        send_json_(req, "400 Bad Request", "{\"success\":false,\"error\":\"request_read_failed\"}");
        return ESP_FAIL;
      }
      received_total += received;
    }

    StaticJsonDocument<2048> doc;
    DeserializationError error = deserializeJson(doc, body);
    if (error) {
      send_json_(req, "400 Bad Request", "{\"success\":false,\"error\":\"invalid_json\"}");
      return ESP_OK;
    }

    const int protocol = doc["protocol"] | 1;
    if (protocol < 1 || protocol > 2) {
      send_json_(req, "400 Bad Request", "{\"success\":false,\"error\":\"unsupported_protocol\"}");
      return ESP_OK;
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
    if (sentero_is_placeholder_device_id(device_id)) device_id = sentero_default_device_id();
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
      send_json_(req, "400 Bad Request", "{\"success\":false,\"error\":\"missing_required_fields\"}");
      return ESP_OK;
    }

    nvs_handle_t prefs;
    if (nvs_open(SENTERO_NVS_NAMESPACE, NVS_READWRITE, &prefs) != ESP_OK) {
      send_json_(req, "500 Internal Server Error", "{\"success\":false,\"error\":\"nvs_open_failed\"}");
      return ESP_OK;
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

    char response[180];
    snprintf(response, sizeof(response),
             "{\"success\":true,\"device_id\":\"%s\",\"model\":\"%s\",\"firmware\":\"%s\"}",
             device_id.c_str(), SENTERO_DEVICE_MODEL, SENTERO_FIRMWARE_VERSION);
    send_json_(req, "200 OK", response);
    restart_at_ms_ = millis() + 1500;
    return ESP_OK;
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

  void send_json_(httpd_req_t *req, const char *status, const char *body) {
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, body);
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

  String topic_(const Config &config, const char *suffix) {
    return config.topic_prefix + "/" + config.device_id + "/" + suffix;
  }

  String availability_payload_(const Config &config, const char *status) {
    StaticJsonDocument<160> doc;
    doc["device_id"] = config.device_id;
    doc["status"] = status;
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

    StaticJsonDocument<1024> doc;
    const String display_name = config.friendly_name.length() > 0
        ? config.friendly_name
        : String("C1001 Praesenz");
    doc["device_id"] = config.device_id;
    doc["name"] = display_name;
    doc["type"] = SENTERO_SENSOR_TYPE;
    doc["manufacturer"] = SENTERO_MANUFACTURER;
    doc["model"] = SENTERO_DEVICE_MODEL;
    doc["firmware"] = SENTERO_FIRMWARE_VERSION;
    JsonArray capabilities = doc.createNestedArray("capabilities");
    capabilities.add("presence");
    capabilities.add("motion");
    capabilities.add("fall_detection");
    capabilities.add("signal_quality");
    doc["presence"] = sensor.presence;
    doc["fall_detected"] = sensor.fall_detected;
    doc["motion"] = sensor.motion;
    doc["moving_range"] = sensor.moving_range;
    doc["work_mode"] = sensor.work_mode;
    doc["sensor_ready"] = sensor.ready;
    doc["sensor_status"] = sensor.status;
    doc["setup_attempts"] = sensor.setup_attempts;
    doc["last_sensor_update_ms"] = sensor.last_update_ms;
    doc["power_source"] = "usb";
    doc["signal_quality"] = signal_quality_();
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
    snprintf(signature, sizeof(signature), "%u|%u|%s|%u|%u|%u|%s",
             sensor.ready ? 1 : 0,
             sensor.presence ? 1 : 0,
             sensor.motion == nullptr ? "" : sensor.motion,
             sensor.moving_range,
             sensor.work_mode,
             sensor.fall_detected ? 1 : 0,
             sensor.status == nullptr ? "" : sensor.status);
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

  void handle_mqtt_message_(const std::string &topic, const std::string &payload) {
    Config config;
    if (!load_config_(config)) return;
    if (String(topic.c_str()) != topic_(config, "command")) return;

    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, payload)) return;
    const char *command = doc["command"] | "";
    if (strcmp(command, "factory_reset") != 0) return;

    factory_reset_(&config);
  }

  void factory_reset_(const Config *config) {
    ESP_LOGW(SENTERO_LOG_TAG, "Factory Reset: Sentero Provisioning-Daten werden geloescht");

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
