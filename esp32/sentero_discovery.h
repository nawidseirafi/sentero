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
             "\"model\":\"C1001\","
             "\"firmware\":\"1.0.0\","
             "\"sensor_type\":\"presence_radar\","
             "\"http_port\":80,"
             "\"capabilities\":["
             "\"presence\","
             "\"fall_detection\","
             "\"breathing_detection\","
             "\"respiration_rate\","
             "\"power_source\","
             "\"signal_quality\"]}",
             device_id.c_str());

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
  uint32_t last_published_sensor_update_ms_{0};
  uint32_t restart_at_ms_{0};
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

  void start_http_once_() {
    if (server_ != nullptr) return;
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
    const char *mqtt_host = mqtt["host"] | "";
    const char *wifi_ssid = doc["wifi"]["ssid"] | "";
    const char *wifi_password = doc["wifi"]["password"] | "";
    String device_id = device["device_id"] | "";
    device_id.trim();
    if (sentero_is_placeholder_device_id(device_id)) device_id = sentero_default_device_id();
    const char *friendly_name = device["friendly_name"] | device["display_name"] | "";
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
    nvs_set_u16(prefs, "mqtt_port", mqtt["port"] | 1883);
    sentero_nvs_put_string(prefs, "mqtt_user", mqtt["username"] | "");
    sentero_nvs_put_string(prefs, "mqtt_pass", mqtt["password"] | "");
    sentero_nvs_put_string(prefs, "topic_prefix", mqtt["topic_prefix"] | "sentero");
    sentero_nvs_put_string(prefs, "device_id", device_id.c_str());
    sentero_nvs_put_string(prefs, "friendly", friendly_name);
    sentero_nvs_put_string(prefs, "room_id", device["room_id"] | "");
    sentero_nvs_put_string(prefs, "token", device["token"] | "");
    nvs_commit(prefs);
    nvs_close(prefs);

    mqtt_configured_ = false;
    if (mqtt_ != nullptr) {
      esp_mqtt_client_stop(mqtt_);
      esp_mqtt_client_destroy(mqtt_);
      mqtt_ = nullptr;
      mqtt_connected_ = false;
    }

    apply_wifi_config_(wifi_ssid, wifi_password);

    char response[180];
    snprintf(response, sizeof(response),
             "{\"success\":true,\"device_id\":\"%s\",\"model\":\"C1001\",\"firmware\":\"1.0.0\"}",
             device_id.c_str());
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
    const C1001Snapshot sensor = c1001_get_snapshot();
    const bool sensor_changed = sensor.last_update_ms != last_published_sensor_update_ms_;
    if ((sensor_changed && now - last_state_publish_ms_ >= 1000) ||
        now - last_state_publish_ms_ >= 10000) {
      last_state_publish_ms_ = now;
      last_published_sensor_update_ms_ = sensor.last_update_ms;
      publish_state_(config);
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
        publish_state_(config);
        last_state_publish_ms_ = millis();
        last_published_sensor_update_ms_ = c1001_get_snapshot().last_update_ms;
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
    config.topic_prefix.trim();
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

    StaticJsonDocument<512> doc;
    doc["device_id"] = config.device_id;
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
    doc["model"] = "C1001";
    doc["firmware"] = "1.0.0";
    if (config.friendly_name.length() > 0) doc["friendly_name"] = config.friendly_name;
    if (config.room_id.length() > 0) doc["room_id"] = config.room_id;

    String payload;
    serializeJson(doc, payload);
    publish_(topic_(config, "state"), payload, true);
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
