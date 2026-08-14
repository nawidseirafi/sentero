#pragma once

#include "esphome.h"
#include <cstring>
#include <cstdio>

struct C1001Snapshot {
  bool ready{false};
  bool presence{false};
  bool fall_detected{false};
  bool hp_led{false};
  bool fall_led{false};
  bool hp_led_known{false};
  bool fall_led_known{false};
  const char *motion{"Nicht bereit"};
  const char *status{"Startet"};
  uint16_t moving_range{0};
  uint16_t work_mode{0};
  uint16_t presence_raw{0};
  uint16_t motion_raw{0};
  uint16_t fall_raw{0};
  uint32_t setup_attempts{0};
  uint32_t poll_count{0};
  uint32_t poll_ok_count{0};
  uint32_t poll_error_count{0};
  uint8_t read_errors{0};
  uint32_t stuck_active_resets{0};
  uint32_t stuck_presence_resets{0};
  uint32_t stuck_inactive_resets{0};
  uint32_t stuck_active_suppressed{0};
  uint32_t last_update_ms{0};
  uint32_t last_value_change_ms{0};
  uint32_t last_poll_ms{0};
  uint32_t last_poll_ok_ms{0};
  uint32_t last_frame_ms{0};
  uint32_t uart_byte_count{0};
  uint32_t valid_frame_count{0};
  uint32_t parser_resync_count{0};
  uint32_t frame_timeout_count{0};
  uint32_t frame_checksum_error_count{0};
  const char *last_frame{nullptr};
};

class C1001Bridge {
 public:
  void update(esphome::uart::UARTComponent *uart,
              esphome::binary_sensor::BinarySensor *presence,
              esphome::binary_sensor::BinarySensor *fall,
              esphome::text_sensor::TextSensor *motion,
              esphome::text_sensor::TextSensor *status,
              esphome::text_sensor::TextSensor *last_frame,
              esphome::sensor::Sensor *moving_range,
              esphome::sensor::Sensor *work_mode,
              esphome::sensor::Sensor *attempts) {
    uart_ = uart;
    presence_ = presence;
    fall_ = fall;
    motion_ = motion;
    status_ = status;
    last_frame_ = last_frame;
    moving_range_ = moving_range;
    work_mode_ = work_mode;
    attempts_ = attempts;

    publish_defaults_once_();
    drain_uart_();

    const uint32_t now = millis();
    if (now < 10000) {
      publish_status_("Warte auf Sensorstart");
      return;
    }

    if (mode_switch_wait_until_ != 0 && now < mode_switch_wait_until_) {
      publish_status_("Fall-Modus gesetzt, Sensor startet neu");
      return;
    }
    mode_switch_wait_until_ = 0;

    if (!ready_) {
      if (last_setup_attempt_ms_ == 0 || now - last_setup_attempt_ms_ >= SETUP_RETRY_INTERVAL_MS) {
        last_setup_attempt_ms_ = now;
        setup_sensor_();
      }
      return;
    }

    if (last_active_poll_ms_ == 0 || now - last_active_poll_ms_ >= ACTIVE_POLL_INTERVAL_MS) {
      last_active_poll_ms_ = now;
      poll_next_value_();
    }
  }

  bool set_fall_led(bool enabled) {
    if (!set_u8_(0x01, 0x04, enabled ? 1 : 0, "FALL LED gesetzt")) return false;
    snapshot_.fall_led = enabled;
    snapshot_.fall_led_known = true;
    snapshot_.last_update_ms = millis();
    return true;
  }

  bool set_hp_led(bool enabled) {
    if (!set_u8_(0x01, 0x03, enabled ? 1 : 0, "HP LED gesetzt")) return false;
    snapshot_.hp_led = enabled;
    snapshot_.hp_led_known = true;
    snapshot_.last_update_ms = millis();
    return true;
  }

  void set_install_height(uint16_t centimeters) {
    uint8_t payload[2] = {
        static_cast<uint8_t>((centimeters >> 8) & 0xFF),
        static_cast<uint8_t>(centimeters & 0xFF),
    };
    set_payload_(0x06, 0x02, payload, sizeof(payload), "Montagehoehe gesetzt");
  }

  void set_fall_time(uint32_t seconds) {
    uint8_t payload[4] = {
        static_cast<uint8_t>((seconds >> 24) & 0xFF),
        static_cast<uint8_t>((seconds >> 16) & 0xFF),
        static_cast<uint8_t>((seconds >> 8) & 0xFF),
        static_cast<uint8_t>(seconds & 0xFF),
    };
    set_payload_(0x83, 0x0C, payload, sizeof(payload), "Fallzeit gesetzt");
  }

  void set_unmanned_time(uint32_t seconds) {
    uint8_t payload[4] = {
        static_cast<uint8_t>((seconds >> 24) & 0xFF),
        static_cast<uint8_t>((seconds >> 16) & 0xFF),
        static_cast<uint8_t>((seconds >> 8) & 0xFF),
        static_cast<uint8_t>(seconds & 0xFF),
    };
    set_payload_(0x80, 0x12, payload, sizeof(payload), "Abwesenheitszeit gesetzt");
  }

  void set_residence_time(uint32_t seconds) {
    uint8_t payload[4] = {
        static_cast<uint8_t>((seconds >> 24) & 0xFF),
        static_cast<uint8_t>((seconds >> 16) & 0xFF),
        static_cast<uint8_t>((seconds >> 8) & 0xFF),
        static_cast<uint8_t>(seconds & 0xFF),
    };
    set_payload_(0x83, 0x0A, payload, sizeof(payload), "Verweilzeit gesetzt");
  }

  void set_fall_sensitivity(uint8_t sensitivity) {
    if (sensitivity > 3) sensitivity = 3;
    set_u8_(0x83, 0x0D, sensitivity, "Sturz-Empfindlichkeit gesetzt");
  }

  void reset_sensor() {
    reset_sensor_("Sensor startet neu", "Reset fehlgeschlagen");
  }

  bool reset_sensor_(const char *success_status, const char *failure_status) {
    uint8_t payload = QUERY_VALUE;
    response_data_.clear();
    if (send_command_(0x01, 0x02, &payload, 1, response_data_, 700)) {
      ready_ = false;
      last_setup_attempt_ms_ = 0;
      last_active_poll_ms_ = 0;
      snapshot_.ready = false;
      snapshot_.last_value_change_ms = millis();
      mode_switch_wait_until_ = millis() + 10000;
      publish_status_(success_status);
      return true;
    } else {
      publish_status_(failure_status);
      return false;
    }
  }

  C1001Snapshot snapshot() const {
    return snapshot_;
  }

 private:
  static constexpr uint8_t FALL_MODE = 0x01;
  static constexpr uint8_t QUERY_VALUE = 0x0F;

  esphome::uart::UARTComponent *uart_{nullptr};
  esphome::binary_sensor::BinarySensor *presence_{nullptr};
  esphome::binary_sensor::BinarySensor *fall_{nullptr};
  esphome::text_sensor::TextSensor *motion_{nullptr};
  esphome::text_sensor::TextSensor *status_{nullptr};
  esphome::text_sensor::TextSensor *last_frame_{nullptr};
  esphome::sensor::Sensor *moving_range_{nullptr};
  esphome::sensor::Sensor *work_mode_{nullptr};
  esphome::sensor::Sensor *attempts_{nullptr};

  std::vector<uint8_t> rx_;
  std::vector<uint8_t> response_data_;
  bool defaults_published_{false};
  bool ready_{false};
  uint32_t setup_attempts_{0};
  uint8_t consecutive_read_errors_{0};
  bool inactive_probe_done_{false};
  uint32_t mode_switch_wait_until_{0};
  uint8_t poll_step_{0};
  uint8_t slow_poll_step_{0};
  uint32_t last_setup_attempt_ms_{0};
  uint32_t last_active_poll_ms_{0};
  C1001Snapshot snapshot_;
  uint32_t last_status_publish_ms_{0};
  uint32_t last_frame_publish_ms_{0};
  uint32_t last_rx_byte_ms_{0};
  bool active_stuck_suppressed_{false};

  static constexpr uint32_t SETUP_RETRY_INTERVAL_MS = 2000;
  static constexpr uint32_t ACTIVE_POLL_INTERVAL_MS = 2000;
  static constexpr uint32_t FRAME_TIMEOUT_MS = 100;
  static constexpr uint32_t STUCK_ACTIVE_SUPPRESS_MS = 3 * 60 * 1000;
  static constexpr size_t MAX_FRAME_SIZE = 80;
  char last_frame_hex_[(MAX_FRAME_SIZE * 3) + 1]{0};

  void publish_defaults_once_() {
    if (defaults_published_) return;
    rx_.reserve(MAX_FRAME_SIZE);
    response_data_.reserve(MAX_FRAME_SIZE);
    presence_->publish_state(false);
    fall_->publish_state(false);
    motion_->publish_state("Nicht bereit");
    status_->publish_state("Startet");
    last_frame_->publish_state("");
    moving_range_->publish_state(0);
    work_mode_->publish_state(0);
    attempts_->publish_state(0);
    snapshot_ = C1001Snapshot{};
    snapshot_.last_update_ms = millis();
    snapshot_.last_value_change_ms = millis();
    defaults_published_ = true;
  }

  void setup_sensor_() {
    consecutive_read_errors_ = 0;
    setup_attempts_++;
    attempts_->publish_state(setup_attempts_);
    snapshot_.setup_attempts = setup_attempts_;
    snapshot_.last_update_ms = millis();

    uint16_t mode = 0;
    if (!query_u16_(0x02, 0xA8, mode, 700)) {
      ready_ = false;
      publish_status_("Keine Antwort auf Arbeitsmodus");
      publish_not_ready_values_();
      return;
    }

    publish_work_mode_(mode);
    if (mode != FALL_MODE) {
      uint8_t payload = FALL_MODE;
      response_data_.clear();
      send_command_(0x02, 0x08, &payload, 1, response_data_, 500);
      ready_ = false;
      snapshot_.ready = false;
      mode_switch_wait_until_ = millis() + 10000;
      publish_status_("Setze Fall-Modus");
      publish_not_ready_values_();
      return;
    }

    ready_ = true;
    last_active_poll_ms_ = 0;
    snapshot_.ready = true;
    snapshot_.last_update_ms = millis();
    publish_status_("OK");
  }

  void poll_next_value_() {
    // Alle schnellen Nutzwerte in einem Poll-Zyklus abfragen.
    // Die Funktion selbst wird weiterhin nur alle ACTIVE_POLL_INTERVAL_MS aufgerufen,
    // aber Presence/Motion/Range/Fall werden dadurch nicht mehr auf 8s gestreckt.
    uint16_t value = 0;
    uint8_t ok_count = 0;
    uint8_t fail_count = 0;
    snapshot_.poll_count++;
    snapshot_.last_poll_ms = millis();

    if (query_presence_(value)) ok_count++; else fail_count++;
    drain_uart_();

    if (query_motion_(value)) ok_count++; else fail_count++;
    drain_uart_();

    if (query_moving_range_(value)) ok_count++; else fail_count++;
    drain_uart_();

    if (query_fall_(value)) ok_count++; else fail_count++;
    drain_uart_();

    // Arbeitsmodus nur langsam pruefen, damit der Sensor nicht unnoetig belastet wird.
    if (slow_poll_step_++ >= 4) {
      slow_poll_step_ = 0;
      if (query_work_mode_(value)) ok_count++; else fail_count++;
      drain_uart_();
    }

    if (fail_count == 0 || ok_count > 0) {
      snapshot_.poll_ok_count += ok_count;
      snapshot_.last_poll_ok_ms = millis();
      consecutive_read_errors_ = 0;
      snapshot_.read_errors = 0;
      check_stuck_active_();
      if (snapshot_.ready) publish_status_("OK");
    } else {
      snapshot_.poll_error_count++;
      consecutive_read_errors_++;
      snapshot_.read_errors = consecutive_read_errors_;
      if (consecutive_read_errors_ >= 4) {
        ready_ = false;
        last_setup_attempt_ms_ = 0;
        snapshot_.ready = false;
        rx_.clear();
        publish_not_ready_values_();
        publish_status_("Sensor neu synchronisieren");
        consecutive_read_errors_ = 0;
        snapshot_.read_errors = 0;
      } else {
        publish_status_("Lesefehler");
      }
    }
  }


  bool query_presence_(uint16_t &value) {
    const bool ok = query_u16_(0x80, 0x81, value, 500);
    if (!ok) return false;
    snapshot_.presence_raw = value;
    if (active_stuck_suppressed_) {
      if (value == 0) clear_active_suppression_();
      else snapshot_.last_update_ms = millis();
    }
    if (!active_stuck_suppressed_) publish_presence_(value == 1);
    return true;
  }

  bool query_motion_(uint16_t &value) {
    const bool ok = query_u16_(0x80, 0x82, value, 500);
    if (!ok) return false;
    snapshot_.motion_raw = value;
    if (active_stuck_suppressed_) {
      if (value != 2) clear_active_suppression_();
      else snapshot_.last_update_ms = millis();
    }
    if (!active_stuck_suppressed_) publish_motion_(motion_text_(value));
    return true;
  }

  bool query_moving_range_(uint16_t &value) {
    const bool ok = query_u16_(0x80, 0x83, value, 500);
    if (!ok) return false;
    if (!active_stuck_suppressed_) publish_moving_range_(value);
    else snapshot_.last_update_ms = millis();
    return true;
  }

  bool query_fall_(uint16_t &value) {
    const bool ok = query_u16_(0x83, 0x81, value, 500);
    if (!ok) return false;
    snapshot_.fall_raw = value;
    publish_fall_(value == 1);
    return true;
  }

  bool query_work_mode_(uint16_t &value) {
    const bool ok = query_u16_(0x02, 0xA8, value, 500);
    if (!ok) return false;
    publish_work_mode_(value);
    return true;
  }

  bool set_u8_(uint8_t control, uint8_t command, uint8_t value, const char *status_text) {
    return set_payload_(control, command, &value, 1, status_text);
  }

  bool set_payload_(uint8_t control, uint8_t command, const uint8_t *payload,
                    uint16_t payload_len, const char *status_text) {
    if (uart_ == nullptr) return false;

    response_data_.clear();
    if (send_command_(control, command, payload, payload_len, response_data_, 700)) {
      publish_status_(status_text);
      return true;
    } else {
      publish_status_("Einstellung fehlgeschlagen");
      return false;
    }
  }

  void publish_not_ready_values_() {
    snapshot_.ready = false;
    publish_presence_(false);
    publish_fall_(false);
    publish_motion_("Nicht bereit");
    publish_moving_range_(0);
  }

  void mark_value_change_() {
    snapshot_.last_value_change_ms = millis();
    inactive_probe_done_ = false;
    active_stuck_suppressed_ = false;
  }


  void check_stuck_active_() {
    if (!snapshot_.ready || !snapshot_.presence) return;
    if (strcmp(snapshot_.motion == nullptr ? "" : snapshot_.motion, "Active") != 0) return;
    const uint32_t now = millis();
    if (now - snapshot_.last_value_change_ms < STUCK_ACTIVE_SUPPRESS_MS) return;
    if (active_stuck_suppressed_) return;

    active_stuck_suppressed_ = true;
    snapshot_.stuck_active_suppressed++;
    snapshot_.ready = false;
    snapshot_.presence = false;
    snapshot_.motion = "Nicht bereit";
    snapshot_.moving_range = 0;
    snapshot_.last_update_ms = now;
    presence_->publish_state(false);
    motion_->publish_state("Nicht bereit");
    moving_range_->publish_state(0);
    publish_status_("Sensorwert haengt aktiv");
  }

  void clear_active_suppression_() {
    active_stuck_suppressed_ = false;
    ready_ = true;
    snapshot_.ready = true;
    mark_value_change_();
  }

  const char *motion_text_(uint16_t value) {
    switch (value) {
      case 0:
        return "None";
      case 1:
        return "Still";
      case 2:
        return "Active";
      default:
        return "Unknown";
    }
  }

  bool query_u16_(uint8_t control, uint8_t command, uint16_t &value, uint32_t timeout_ms) {
    response_data_.clear();
    if (!send_command_(control, command, &QUERY_VALUE, 1, response_data_, timeout_ms)) return false;
    if (response_data_.size() == 0) return false;

    if (response_data_.size() >= 2) {
      value = (static_cast<uint16_t>(response_data_[0]) << 8) | response_data_[1];
    } else {
      value = response_data_[0];
    }
    return true;
  }

  bool send_command_(uint8_t control, uint8_t command, const uint8_t *payload,
                     uint16_t payload_len, std::vector<uint8_t> &data,
                     uint32_t timeout_ms) {
    drain_uart_();

    if (payload_len > 16) return false;
    uint8_t frame[25];
    size_t pos = 0;
    frame[pos++] = 0x53;
    frame[pos++] = 0x59;
    frame[pos++] = control;
    frame[pos++] = command;
    frame[pos++] = (payload_len >> 8) & 0xFF;
    frame[pos++] = payload_len & 0xFF;
    for (uint16_t i = 0; i < payload_len; i++) frame[pos++] = payload[i];
    frame[pos++] = checksum_(frame, pos);
    frame[pos++] = 0x54;
    frame[pos++] = 0x43;

    uart_->write_array(frame, pos);
    return wait_for_response_(control, command, data, timeout_ms);
  }

  bool wait_for_response_(uint8_t control, uint8_t command, std::vector<uint8_t> &data,
                          uint32_t timeout_ms) {
    const uint32_t started = millis();
    while (millis() - started < timeout_ms) {
      uint8_t b = 0;
      if (uart_->available() && uart_->read_byte(&b)) {
        if (parse_byte_(b, control, command, &data)) return true;
      } else {
        delay(2);
      }
    }
    return false;
  }

  void drain_uart_() {
    if (uart_ == nullptr) return;
    uint8_t b = 0;
    while (uart_->available()) {
      if (!uart_->read_byte(&b)) break;
      snapshot_.uart_byte_count++;
      parse_byte_(b, 0xFF, 0xFF, nullptr);
    }
  }

  bool parse_byte_(uint8_t b, uint8_t wanted_control, uint8_t wanted_command,
                   std::vector<uint8_t> *wanted_data) {
    const uint32_t now = millis();
    if (!rx_.empty() && now - last_rx_byte_ms_ > FRAME_TIMEOUT_MS) {
      snapshot_.frame_timeout_count++;
      rx_.clear();
    }
    last_rx_byte_ms_ = now;

    if (rx_.empty()) {
      if (b == 0x53) rx_.push_back(b);
      return false;
    }

    if (rx_.size() == 1) {
      if (b == 0x59) {
        rx_.push_back(b);
      } else if (b == 0x53) {
        rx_[0] = b;
      } else {
        rx_.clear();
      }
      return false;
    }

    rx_.push_back(b);
    if (rx_.size() > MAX_FRAME_SIZE) {
      resync_parser_();
      return false;
    }

    if (rx_.size() < 9) return false;

    const uint16_t len = (static_cast<uint16_t>(rx_[4]) << 8) | rx_[5];
    const uint16_t total_len = 9 + len;
    if (total_len > MAX_FRAME_SIZE) {
      resync_parser_();
      return false;
    }
    if (rx_.size() != total_len) return false;

    const bool valid = frame_is_valid_(len);
    if (!valid) {
      snapshot_.frame_checksum_error_count++;
      resync_parser_();
      return false;
    }

    publish_frame_hex_();
    snapshot_.valid_frame_count++;
    snapshot_.last_frame_ms = millis();

    // Wichtig: Jeden gueltigen bekannten Frame auswerten, nicht nur die Antwort
    // auf die gerade aktive Abfrage. Sonst koennen Presence/Motion/Fall nach
    // dem ersten Startwert scheinbar einfrieren, obwohl der UART weiter Frames liefert.
    handle_known_frame_(len);

    const uint8_t control = rx_[2];
    const uint8_t command = rx_[3];
    if (control == wanted_control && command == wanted_command && wanted_data != nullptr) {
      wanted_data->assign(rx_.begin() + 6, rx_.begin() + 6 + len);
      rx_.clear();
      return true;
    }

    rx_.clear();
    return false;
  }


  void resync_parser_() {
    snapshot_.parser_resync_count++;
    for (size_t i = 1; i + 1 < rx_.size(); i++) {
      if (rx_[i] == 0x53 && rx_[i + 1] == 0x59) {
        rx_.erase(rx_.begin(), rx_.begin() + i);
        return;
      }
    }
    const bool keep_header = !rx_.empty() && rx_.back() == 0x53;
    rx_.clear();
    if (keep_header) rx_.push_back(0x53);
  }

  bool frame_is_valid_(uint16_t len) {
    const uint16_t checksum_index = 6 + len;
    const uint16_t end1_index = 7 + len;
    const uint16_t end2_index = 8 + len;
    if (end2_index >= rx_.size()) return false;
    if (rx_[end1_index] != 0x54 || rx_[end2_index] != 0x43) return false;
    return rx_[checksum_index] == checksum_(rx_.data(), checksum_index);
  }

  void handle_known_frame_(uint16_t len) {
    if (len < 1) return;
    const uint8_t control = rx_[2];
    const uint8_t command = rx_[3];
    const uint16_t value = len >= 2 ? ((static_cast<uint16_t>(rx_[6]) << 8) | rx_[7]) : rx_[6];

    if (control == 0x80 && command == 0x81) {
      publish_presence_(value == 1);
    } else if (control == 0x80 && command == 0x82) {
      publish_motion_(motion_text_(value));
    } else if (control == 0x80 && command == 0x83) {
      publish_moving_range_(value);
    } else if (control == 0x83 && command == 0x81) {
      publish_fall_(value == 1);
    } else if (control == 0x02 && command == 0xA8) {
      publish_work_mode_(value);
    }
  }

  void publish_frame_hex_() {
    const uint32_t now = millis();
    if (now - last_frame_publish_ms_ < 1000) return;
    last_frame_publish_ms_ = now;

    size_t pos = 0;
    for (size_t i = 0; i < rx_.size() && pos + 3 < sizeof(last_frame_hex_); i++) {
      if (i > 0) last_frame_hex_[pos++] = ':';
      snprintf(last_frame_hex_ + pos, sizeof(last_frame_hex_) - pos, "%02X", rx_[i]);
      pos += 2;
    }
    last_frame_hex_[pos] = 0;
    snapshot_.last_frame = last_frame_hex_;
    last_frame_->publish_state(last_frame_hex_);
  }

  uint8_t checksum_(const uint8_t *buf, size_t len) {
    uint16_t sum = 0;
    for (size_t i = 0; i < len; i++) sum += buf[i];
    return sum & 0xFF;
  }

  void publish_status_(const char *value) {
    const uint32_t now = millis();
    const char *current = snapshot_.status == nullptr ? "" : snapshot_.status;
    const char *next = value == nullptr ? "" : value;
    if (strcmp(current, next) != 0 || now - last_status_publish_ms_ >= 1000) {
      status_->publish_state(value);
      last_status_publish_ms_ = now;
    }
    snapshot_.status = value;
    snapshot_.last_update_ms = now;
  }

  void publish_presence_(bool value) {
    if (snapshot_.presence != value) {
      mark_value_change_();
      presence_->publish_state(value);
      snapshot_.presence = value;
    }
    snapshot_.last_update_ms = millis();
  }

  void publish_fall_(bool value) {
    if (snapshot_.fall_detected != value) {
      mark_value_change_();
      fall_->publish_state(value);
      snapshot_.fall_detected = value;
    }
    snapshot_.last_update_ms = millis();
  }

  void publish_motion_(const char *value) {
    const char *current = snapshot_.motion == nullptr ? "" : snapshot_.motion;
    const char *next = value == nullptr ? "" : value;
    if (strcmp(current, next) != 0) {
      mark_value_change_();
      motion_->publish_state(value);
      snapshot_.motion = value;
    }
    snapshot_.last_update_ms = millis();
  }

  void publish_moving_range_(uint16_t value) {
    if (snapshot_.moving_range != value) {
      mark_value_change_();
      moving_range_->publish_state(value);
      snapshot_.moving_range = value;
    }
    snapshot_.last_update_ms = millis();
  }

  void publish_work_mode_(uint16_t value) {
    if (snapshot_.work_mode != value) {
      mark_value_change_();
      work_mode_->publish_state(value);
      snapshot_.work_mode = value;
    }
    snapshot_.last_update_ms = millis();
  }
};

static C1001Bridge c1001_bridge;

inline void c1001_update(esphome::uart::UARTComponent *uart,
                         esphome::binary_sensor::BinarySensor *presence,
                         esphome::binary_sensor::BinarySensor *fall,
                         esphome::text_sensor::TextSensor *motion,
                         esphome::text_sensor::TextSensor *status,
                         esphome::text_sensor::TextSensor *last_frame,
                         esphome::sensor::Sensor *moving_range,
                         esphome::sensor::Sensor *work_mode,
                         esphome::sensor::Sensor *attempts) {
  c1001_bridge.update(uart, presence, fall, motion, status, last_frame,
                      moving_range, work_mode, attempts);
}

inline bool c1001_set_fall_led(bool enabled) {
  return c1001_bridge.set_fall_led(enabled);
}

inline bool c1001_set_hp_led(bool enabled) {
  return c1001_bridge.set_hp_led(enabled);
}

inline void c1001_set_install_height(float centimeters) {
  c1001_bridge.set_install_height(static_cast<uint16_t>(centimeters));
}

inline void c1001_set_fall_time(float seconds) {
  c1001_bridge.set_fall_time(static_cast<uint32_t>(seconds));
}

inline void c1001_set_unmanned_time(float seconds) {
  c1001_bridge.set_unmanned_time(static_cast<uint32_t>(seconds));
}

inline void c1001_set_residence_time(float seconds) {
  c1001_bridge.set_residence_time(static_cast<uint32_t>(seconds));
}

inline void c1001_set_fall_sensitivity(float sensitivity) {
  c1001_bridge.set_fall_sensitivity(static_cast<uint8_t>(sensitivity));
}

inline void c1001_reset_sensor() {
  c1001_bridge.reset_sensor();
}

inline C1001Snapshot c1001_get_snapshot() {
  return c1001_bridge.snapshot();
}
