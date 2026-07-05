#pragma once

#include "esphome.h"

// ---------------------------------------------------------------------------
// Anders als c1001_bridge.h implementiert dieser Adapter KEIN eigenes
// UART-Protokoll. Das offizielle ESPHome-Component "seeed_mr60bha2"
// (siehe YAML: platform: seeed_mr60bha2) übernimmt bereits das komplette
// Framing/Parsing des Seeed-Binärprotokolls und stellt die Werte ganz normal
// als ESPHome binary_sensor/sensor Entities bereit.
//
// Dieser Adapter liest nur periodisch deren aktuellen Zustand aus und
// spiegelt ihn in ein MR60BHA2Snapshot, damit sentero_discovery.h weiterhin
// im gewohnten Snapshot-Muster arbeiten kann (statt direkt gegen
// esphome::sensor::Sensor* zu programmieren).
// ---------------------------------------------------------------------------

struct MR60BHA2Snapshot {
  bool ready{false};
  bool presence{false};
  float breath_rate{0.0f};
  float heart_rate{0.0f};
  float distance{0.0f};
  uint16_t num_targets{0};
  uint32_t last_update_ms{0};
  uint32_t last_value_change_ms{0};
};

class MR60BHA2Bridge {
 public:
  void update(esphome::binary_sensor::BinarySensor *presence,
              esphome::sensor::Sensor *breath_rate,
              esphome::sensor::Sensor *heart_rate,
              esphome::sensor::Sensor *distance,
              esphome::sensor::Sensor *num_targets) {
    bool changed = false;

    const bool new_presence = presence != nullptr && presence->state;
    if (new_presence != snapshot_.presence) changed = true;
    snapshot_.presence = new_presence;

    changed |= assign_if_changed_(snapshot_.breath_rate, read_float_(breath_rate));
    changed |= assign_if_changed_(snapshot_.heart_rate, read_float_(heart_rate));
    changed |= assign_if_changed_(snapshot_.distance, read_float_(distance));

    const uint16_t new_targets = num_targets != nullptr && num_targets->has_state()
                                      ? static_cast<uint16_t>(num_targets->state)
                                      : 0;
    if (new_targets != snapshot_.num_targets) changed = true;
    snapshot_.num_targets = new_targets;

    snapshot_.ready = true;
    snapshot_.last_update_ms = millis();
    if (changed) snapshot_.last_value_change_ms = millis();
  }

  MR60BHA2Snapshot snapshot() const {
    return snapshot_;
  }

 private:
  MR60BHA2Snapshot snapshot_;

  float read_float_(esphome::sensor::Sensor *sensor) {
    if (sensor == nullptr || !sensor->has_state()) return 0.0f;
    return sensor->state;
  }

  bool assign_if_changed_(float &target, float new_value) {
    // Kleine Toleranz, da Float-Werte vom Radar minimal jittern und wir
    // sonst bei jedem Poll "changed" waeren.
    const bool changed = fabsf(target - new_value) > 0.05f;
    target = new_value;
    return changed;
  }
};

static MR60BHA2Bridge mr60bha2_bridge;

inline void mr60bha2_update(esphome::binary_sensor::BinarySensor *presence,
                            esphome::sensor::Sensor *breath_rate,
                            esphome::sensor::Sensor *heart_rate,
                            esphome::sensor::Sensor *distance,
                            esphome::sensor::Sensor *num_targets) {
  mr60bha2_bridge.update(presence, breath_rate, heart_rate, distance, num_targets);
}

inline MR60BHA2Snapshot mr60bha2_get_snapshot() {
  return mr60bha2_bridge.snapshot();
}
