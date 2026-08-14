#pragma once

#include "esphome.h"
#include "esphome/components/uart/uart.h"

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// Sentero MR60BDA2/MR60FDA2 UART-Adapter
//
// Aufgabe dieser Datei:
//   Radar UART lesen
//   MR60FDA2-kompatible Frames dekodieren
//   einen Sentero-kompatiblen Snapshot fuer sentero_discovery.h liefern
//
// Wichtig:
//   Das ist keine transparente UART-Passthrough-Bridge mehr, sondern ein
//   kleiner Sensor-Treiber/Adapter. Provisioning, Discovery und MQTT bleiben
//   unveraendert in sentero_discovery.h.
// ---------------------------------------------------------------------------

struct MR60BDA2Snapshot {
  bool ready{false};
  bool presence{false};
  bool fall_detected{false};
  const char *motion{"None"};
  uint32_t last_update_ms{0};
  uint32_t last_value_change_ms{0};
  uint32_t last_frame_ms{0};
  std::string last_frame{""};
};

class MR60BDA2Bridge {
 public:
  void update(esphome::uart::UARTComponent *uart) {
    snapshot_.last_update_ms = millis();
    if (uart == nullptr) return;

    uint8_t byte = 0;
    while (uart->available() && uart->read_byte(&byte)) {
      parse_byte_(byte);
    }
  }

  MR60BDA2Snapshot snapshot() const {
    return snapshot_;
  }

 private:
  static constexpr const char *TAG = "mr60bda2.bridge";

  static constexpr uint8_t FRAME_HEADER = 0x01;
  static constexpr size_t DATA_BUF_MAX_SIZE = 28;
  static constexpr size_t FRAME_BUF_MAX_SIZE = 37;

  static constexpr uint16_t FRAME_TYPE_FALL = 0x0E02;
  static constexpr uint16_t FRAME_TYPE_PRESENCE = 0x0F09;

  enum FrameState : uint8_t {
    WAIT_HEADER = 0,
    READ_ID_H,
    READ_ID_L,
    READ_LEN_H,
    READ_LEN_L,
    READ_TYPE_H,
    READ_TYPE_L,
    READ_HEAD_CHECKSUM,
    READ_DATA,
    READ_DATA_CHECKSUM,
  };

  MR60BDA2Snapshot snapshot_;
  FrameState state_{WAIT_HEADER};
  uint16_t frame_id_{0};
  uint16_t data_len_{0};
  uint16_t frame_type_{0};
  std::vector<uint8_t> frame_;
  std::vector<uint8_t> data_;

  void reset_parser_() {
    state_ = WAIT_HEADER;
    frame_id_ = 0;
    data_len_ = 0;
    frame_type_ = 0;
    frame_.clear();
    data_.clear();
  }

  static uint8_t checksum_(const uint8_t *data, size_t len) {
    uint8_t checksum = 0;
    for (size_t i = 0; i < len; i++) checksum ^= data[i];
    return static_cast<uint8_t>(~checksum);
  }

  static bool checksum_ok_(const std::vector<uint8_t> &data, uint8_t expected) {
    if (data.empty()) return checksum_(nullptr, 0) == expected;
    return checksum_(data.data(), data.size()) == expected;
  }

  void push_frame_byte_(uint8_t byte) {
    frame_.push_back(byte);
    if (frame_.size() > FRAME_BUF_MAX_SIZE) {
      ESP_LOGD(TAG, "Frame zu lang, Parser wird zurueckgesetzt");
      reset_parser_();
    }
  }

  void parse_byte_(uint8_t byte) {
    switch (state_) {
      case WAIT_HEADER:
        if (byte == FRAME_HEADER) {
          reset_parser_();
          push_frame_byte_(byte);
          state_ = READ_ID_H;
        }
        break;

      case READ_ID_H:
        frame_id_ = static_cast<uint16_t>(byte) << 8;
        push_frame_byte_(byte);
        state_ = READ_ID_L;
        break;

      case READ_ID_L:
        frame_id_ |= byte;
        push_frame_byte_(byte);
        state_ = READ_LEN_H;
        break;

      case READ_LEN_H:
        // Der offizielle ESPHome-Treiber akzeptiert hier nur 0x00.
        // Damit bleiben wir bewusst kompatibel mit dem bekannten MR60FDA2-
        // Protokoll und verwerfen unerwartet grosse Frames.
        if (byte != 0x00) {
          ESP_LOGD(TAG, "Ungueltiges Laengen-High-Byte: 0x%02X", byte);
          reset_parser_();
          if (byte == FRAME_HEADER) {
            push_frame_byte_(byte);
            state_ = READ_ID_H;
          }
          break;
        }
        data_len_ = static_cast<uint16_t>(byte) << 8;
        push_frame_byte_(byte);
        state_ = READ_LEN_L;
        break;

      case READ_LEN_L:
        data_len_ |= byte;
        if (data_len_ > DATA_BUF_MAX_SIZE) {
          ESP_LOGD(TAG, "Datenlaenge zu gross: %u", data_len_);
          reset_parser_();
          break;
        }
        push_frame_byte_(byte);
        data_.clear();
        data_.reserve(data_len_);
        state_ = READ_TYPE_H;
        break;

      case READ_TYPE_H:
        frame_type_ = static_cast<uint16_t>(byte) << 8;
        push_frame_byte_(byte);
        state_ = READ_TYPE_L;
        break;

      case READ_TYPE_L:
        frame_type_ |= byte;
        push_frame_byte_(byte);
        state_ = READ_HEAD_CHECKSUM;
        break;

      case READ_HEAD_CHECKSUM: {
        const bool ok = checksum_ok_(frame_, byte);
        push_frame_byte_(byte);
        if (!ok) {
          ESP_LOGD(TAG, "Header-Checksumme falsch: type=0x%04X", frame_type_);
          reset_parser_();
          break;
        }
        state_ = data_len_ == 0 ? READ_DATA_CHECKSUM : READ_DATA;
        break;
      }

      case READ_DATA:
        data_.push_back(byte);
        push_frame_byte_(byte);
        if (data_.size() >= data_len_) state_ = READ_DATA_CHECKSUM;
        break;

      case READ_DATA_CHECKSUM: {
        const bool ok = checksum_ok_(data_, byte);
        push_frame_byte_(byte);
        if (ok) {
          process_frame_();
        } else {
          ESP_LOGD(TAG, "Daten-Checksumme falsch: type=0x%04X", frame_type_);
        }
        reset_parser_();
        break;
      }
    }
  }

  void process_frame_() {
    snapshot_.ready = true;
    snapshot_.last_frame_ms = millis();
    snapshot_.last_frame = hex_(frame_);

    bool changed = false;

    switch (frame_type_) {
      case FRAME_TYPE_PRESENCE: {
        const bool presence = !data_.empty() && data_[0] != 0x00;
        if (snapshot_.presence != presence) {
          snapshot_.presence = presence;
          changed = true;
        }
        const char *new_motion = presence ? "Detected" : "None";
        if (snapshot_.motion != new_motion) {
          snapshot_.motion = new_motion;
          changed = true;
        }
        ESP_LOGD(TAG, "Presence=%d", presence ? 1 : 0);
        break;
      }

      case FRAME_TYPE_FALL: {
        const bool fall = !data_.empty() && data_[0] != 0x00;
        if (snapshot_.fall_detected != fall) {
          snapshot_.fall_detected = fall;
          changed = true;
        }
        ESP_LOGD(TAG, "Fall=%d", fall ? 1 : 0);
        break;
      }

      default:
        // Bekannte, aber fuer Sentero-State nicht benoetigte Frames, z.B.
        // Parameterantworten, werden als gueltiger Frame gespeichert, aendern
        // aber den Snapshot nicht.
        ESP_LOGV(TAG, "Ignorierter Frame type=0x%04X len=%u", frame_type_, data_len_);
        break;
    }

    if (changed) snapshot_.last_value_change_ms = millis();
  }

  std::string hex_(const std::vector<uint8_t> &data) const {
    static const char *digits = "0123456789ABCDEF";
    std::string out;
    out.reserve(data.size() * 3);
    for (size_t i = 0; i < data.size(); i++) {
      if (i > 0) out.push_back(':');
      out.push_back(digits[(data[i] >> 4) & 0x0F]);
      out.push_back(digits[data[i] & 0x0F]);
    }
    return out;
  }
};

static MR60BDA2Bridge mr60bda2_bridge;

inline void mr60bda2_update(esphome::uart::UARTComponent *uart) {
  mr60bda2_bridge.update(uart);
}

inline MR60BDA2Snapshot mr60bda2_get_snapshot() {
  return mr60bda2_bridge.snapshot();
}
