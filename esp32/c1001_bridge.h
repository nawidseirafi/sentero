#pragma once

#include "esphome.h"

struct C1001Snapshot {
  bool ready{false};
  bool presence{false};
  bool fall_detected{false};
  const char *motion{"Nicht bereit"};
  const char *status{"Startet"};
  uint16_t moving_range{0};
  uint16_t work_mode{0};
  uint32_t setup_attempts{0};
  uint32_t last_update_ms{0};
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
    drain_passive_frames_(25);

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
      setup_sensor_();
      return;
    }

    poll_next_value_();
  }

  void set_fall_led(bool enabled) {
    set_u8_(0x01, 0x04, enabled ? 1 : 0, "FALL LED gesetzt");
  }

  void set_hp_led(bool enabled) {
    set_u8_(0x01, 0x03, enabled ? 1 : 0, "HP LED gesetzt");
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
    uint8_t payload = QUERY_VALUE;
    std::vector<uint8_t> ignored;
    if (send_command_(0x01, 0x02, &payload, 1, ignored, 700)) {
      ready_ = false;
      snapshot_.ready = false;
      mode_switch_wait_until_ = millis() + 10000;
      publish_status_("Sensor startet neu");
    } else {
      publish_status_("Reset fehlgeschlagen");
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
  bool defaults_published_{false};
  bool ready_{false};
  uint32_t setup_attempts_{0};
  uint32_t mode_switch_wait_until_{0};
  uint8_t poll_step_{0};
  C1001Snapshot snapshot_;

  void publish_defaults_once_() {
    if (defaults_published_) return;
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
    defaults_published_ = true;
  }

  void setup_sensor_() {
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
      std::vector<uint8_t> ignored;
      send_command_(0x02, 0x08, &payload, 1, ignored, 500);
      ready_ = false;
      snapshot_.ready = false;
      mode_switch_wait_until_ = millis() + 10000;
      publish_status_("Setze Fall-Modus");
      publish_not_ready_values_();
      return;
    }

    ready_ = true;
    snapshot_.ready = true;
    snapshot_.last_update_ms = millis();
    publish_status_("OK");
  }

  void poll_next_value_() {
    uint16_t value = 0;
    bool ok = false;

    switch (poll_step_) {
      case 0:
        ok = query_u16_(0x80, 0x81, value, 500);
        if (ok) publish_presence_(value == 1);
        break;
      case 1:
        ok = query_u16_(0x80, 0x82, value, 500);
        if (ok) publish_motion_(motion_text_(value));
        break;
      case 2:
        ok = query_u16_(0x80, 0x83, value, 500);
        if (ok) publish_moving_range_(value);
        break;
      case 3:
        ok = query_u16_(0x83, 0x81, value, 500);
        if (ok) publish_fall_(value == 1);
        break;
      default:
        ok = query_u16_(0x02, 0xA8, value, 500);
        if (ok) publish_work_mode_(value);
        break;
    }

    poll_step_ = (poll_step_ + 1) % 5;
    publish_status_(ok ? "OK" : "Lesefehler");
  }

  void set_u8_(uint8_t control, uint8_t command, uint8_t value, const char *status_text) {
    set_payload_(control, command, &value, 1, status_text);
  }

  void set_payload_(uint8_t control, uint8_t command, const uint8_t *payload,
                    uint16_t payload_len, const char *status_text) {
    if (uart_ == nullptr) return;

    std::vector<uint8_t> ignored;
    if (send_command_(control, command, payload, payload_len, ignored, 700)) {
      publish_status_(status_text);
    } else {
      publish_status_("Einstellung fehlgeschlagen");
    }
  }

  void publish_not_ready_values_() {
    snapshot_.ready = false;
    publish_presence_(false);
    publish_fall_(false);
    publish_motion_("Nicht bereit");
    publish_moving_range_(0);
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
    std::vector<uint8_t> data;
    if (!send_command_(control, command, &QUERY_VALUE, 1, data, timeout_ms)) return false;
    if (data.size() == 0) return false;

    if (data.size() >= 2) {
      value = (static_cast<uint16_t>(data[0]) << 8) | data[1];
    } else {
      value = data[0];
    }
    return true;
  }

  bool send_command_(uint8_t control, uint8_t command, const uint8_t *payload,
                     uint16_t payload_len, std::vector<uint8_t> &data,
                     uint32_t timeout_ms) {
    drain_passive_frames_(10);

    std::vector<uint8_t> frame;
    frame.reserve(9 + payload_len);
    frame.push_back(0x53);
    frame.push_back(0x59);
    frame.push_back(control);
    frame.push_back(command);
    frame.push_back((payload_len >> 8) & 0xFF);
    frame.push_back(payload_len & 0xFF);
    for (uint16_t i = 0; i < payload_len; i++) frame.push_back(payload[i]);
    frame.push_back(checksum_(frame.data(), frame.size()));
    frame.push_back(0x54);
    frame.push_back(0x43);

    uart_->write_array(frame.data(), frame.size());
    return wait_for_response_(control, command, data, timeout_ms);
  }

  bool wait_for_response_(uint8_t control, uint8_t command, std::vector<uint8_t> &data,
                          uint32_t timeout_ms) {
    const uint32_t started = millis();
    while (millis() - started < timeout_ms) {
      uint8_t b = 0;
      if (uart_->available() && uart_->read_byte(&b)) {
        if (parse_byte_(b, control, command, data)) return true;
      } else {
        delay(2);
      }
    }
    return false;
  }

  void drain_passive_frames_(uint32_t budget_ms) {
    const uint32_t started = millis();
    while (uart_->available() && millis() - started < budget_ms) {
      uint8_t b = 0;
      if (!uart_->read_byte(&b)) break;
      std::vector<uint8_t> ignored;
      parse_byte_(b, 0xFF, 0xFF, ignored);
    }
  }

  bool parse_byte_(uint8_t b, uint8_t wanted_control, uint8_t wanted_command,
                   std::vector<uint8_t> &wanted_data) {
    if (rx_.empty()) {
      if (b == 0x53) rx_.push_back(b);
      return false;
    }

    if (rx_.size() == 1) {
      if (b == 0x59) {
        rx_.push_back(b);
      } else {
        rx_.clear();
      }
      return false;
    }

    rx_.push_back(b);
    if (rx_.size() > 80) {
      rx_.clear();
      return false;
    }

    if (rx_.size() < 9) return false;

    const uint16_t len = (static_cast<uint16_t>(rx_[4]) << 8) | rx_[5];
    const uint16_t total_len = 9 + len;
    if (total_len > 80) {
      rx_.clear();
      return false;
    }
    if (rx_.size() != total_len) return false;

    const bool valid = frame_is_valid_(len);
    if (!valid) {
      rx_.clear();
      return false;
    }

    publish_frame_hex_();
    handle_known_frame_(len);

    const uint8_t control = rx_[2];
    const uint8_t command = rx_[3];
    if (control == wanted_control && command == wanted_command) {
      wanted_data.assign(rx_.begin() + 6, rx_.begin() + 6 + len);
      rx_.clear();
      return true;
    }

    rx_.clear();
    return false;
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
    char tmp[4];
    std::string out;
    for (size_t i = 0; i < rx_.size(); i++) {
      snprintf(tmp, sizeof(tmp), "%02X", rx_[i]);
      if (i > 0) out += ":";
      out += tmp;
    }
    last_frame_->publish_state(out);
  }

  uint8_t checksum_(const uint8_t *buf, size_t len) {
    uint16_t sum = 0;
    for (size_t i = 0; i < len; i++) sum += buf[i];
    return sum & 0xFF;
  }

  void publish_status_(const char *value) {
    status_->publish_state(value);
    snapshot_.status = value;
    snapshot_.last_update_ms = millis();
  }

  void publish_presence_(bool value) {
    presence_->publish_state(value);
    snapshot_.presence = value;
    snapshot_.last_update_ms = millis();
  }

  void publish_fall_(bool value) {
    fall_->publish_state(value);
    snapshot_.fall_detected = value;
    snapshot_.last_update_ms = millis();
  }

  void publish_motion_(const char *value) {
    motion_->publish_state(value);
    snapshot_.motion = value;
    snapshot_.last_update_ms = millis();
  }

  void publish_moving_range_(uint16_t value) {
    moving_range_->publish_state(value);
    snapshot_.moving_range = value;
    snapshot_.last_update_ms = millis();
  }

  void publish_work_mode_(uint16_t value) {
    work_mode_->publish_state(value);
    snapshot_.work_mode = value;
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

inline void c1001_set_fall_led(bool enabled) {
  c1001_bridge.set_fall_led(enabled);
}

inline void c1001_set_hp_led(bool enabled) {
  c1001_bridge.set_hp_led(enabled);
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