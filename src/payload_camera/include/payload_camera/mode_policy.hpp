#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>

namespace payload_camera {
enum Mode : uint8_t {BOOT = 0, STANDBY = 1, READY = 2, DOWNLINK = 3, SAFE = 4, FAULT = 5};
class ModePolicy {
public:
  Mode mode{BOOT};
  unsigned stage{0};
  std::string reason;
  std::string guard(uint8_t target, bool permitted, bool busy, bool recover) const {
    if (target == BOOT || target > SAFE) { return "mode is internal or unknown"; }
    if (target == SAFE) { return {}; }
    if (target == READY && !permitted) { return "spacecraft imaging permission absent"; }
    if (target == mode && mode != SAFE) { return {}; }
    if (busy) { return "capture activity in progress; cancel or command SAFE"; }
    if (mode == SAFE || mode == FAULT) {
      return target == STANDBY && recover ? "" : "explicit recovery to STANDBY required";
    }
    if (mode == BOOT && target != STANDBY) { return "BOOT must configure to STANDBY"; }
    return {};
  }
  void request(uint8_t target, bool permitted, bool busy, bool recover) {
    const auto error = guard(target, permitted, busy, recover);
    if (!error.empty()) { throw std::runtime_error(error); }
    mode = static_cast<Mode>(target);
    if (!busy) { stage = 0; }
    reason.clear();
  }
  void inhibit(bool fault, const std::string &why) {
    // FAULT remains latched even if a later SAFE or inhibit message arrives.
    mode = fault || mode == FAULT ? FAULT : SAFE;
    reason = why;
  }
  bool can_capture(bool permitted, bool active) const {
    return mode == READY && permitted && active && stage == 0;
  }
  std::string state() const {
    if (mode == READY) {
      if (stage == 1) { return "CAPTURING"; }
      if (stage == 2) { return "PROCESSING"; }
      if (stage == 3) { return "STORING"; }
      return "READY";
    }
    switch (mode) {
      case BOOT: return "BOOT";
      case STANDBY: return "STANDBY";
      case DOWNLINK: return "DOWNLINK";
      case SAFE: return "SAFE";
      case FAULT: return "FAULT";
      default: return "FAULT";
    }
  }
};
}  // namespace payload_camera
