#pragma once

#include "esphome.h"
#include "esphome/components/uart/uart.h"

// ---------------------------------------------------------------------------
// Direkter UART-Adapter fuer MR60BDA2. Aktuell puffert er die Rohframes und
// liefert daraus einen Sentero-kompatiblen Snapshot. Presence/Motion/Fall
// bleiben bis zum finalen Protokoll-Mapping bewusst neutral.
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
    bool changed = false;
    if (uart != nullptr) {
      uint8_t byte;
      while (uart->available() && uart->read_byte(&byte)) {
        rx_.push_back(byte);
        if (rx_.size() > 96) rx_.erase(rx_.begin());
        snapshot_.last_frame_ms = millis();
      }
      if (!rx_.empty()) snapshot_.last_frame = hex_(rx_);
    }

    snapshot_.ready = snapshot_.last_frame_ms > 0;
    snapshot_.last_update_ms = millis();
    if (changed) snapshot_.last_value_change_ms = millis();
  }

  MR60BDA2Snapshot snapshot() const {
    return snapshot_;
  }

 private:
  MR60BDA2Snapshot snapshot_;
  std::vector<uint8_t> rx_;

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
