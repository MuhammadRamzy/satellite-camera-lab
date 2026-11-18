#include <gtest/gtest.h>
#include "payload_camera/mode_policy.hpp"
using namespace payload_camera;
TEST(Modes, BootPermissionAndInternalGuards) {
  ModePolicy p;
  EXPECT_THROW(p.request(READY, true, false, false), std::runtime_error);
  EXPECT_THROW(p.request(BOOT, false, false, false), std::runtime_error);
  EXPECT_THROW(p.request(FAULT, false, false, false), std::runtime_error);
  p.request(STANDBY, false, false, false);
  EXPECT_THROW(p.request(READY, false, false, false), std::runtime_error);
  p.request(READY, true, false, false);
  EXPECT_TRUE(p.can_capture(true, true));
  EXPECT_FALSE(p.can_capture(false, true));
  EXPECT_FALSE(p.can_capture(true, false));
  EXPECT_THROW(p.request(READY, false, false, false), std::runtime_error);
}
TEST(Modes, BusyGuardAndSafePreemption) {
  ModePolicy p;
  p.request(STANDBY, false, false, false); p.request(READY, true, false, false);
  p.stage = 1;
  EXPECT_EQ(p.state(), "CAPTURING");
  EXPECT_FALSE(p.can_capture(true, true));
  EXPECT_THROW(p.request(DOWNLINK, true, true, false), std::runtime_error);
  EXPECT_THROW(p.request(STANDBY, true, true, false), std::runtime_error);
  p.request(SAFE, false, true, false);
  EXPECT_EQ(p.state(), "SAFE");
  EXPECT_THROW(p.request(STANDBY, true, true, true), std::runtime_error);
}
TEST(Modes, LatchedFaultAndExplicitRecovery) {
  ModePolicy p;
  p.inhibit(true, "lease expired");
  p.inhibit(false, "imaging disabled");
  EXPECT_EQ(p.mode, FAULT);
  EXPECT_THROW(p.request(READY, true, false, true), std::runtime_error);
  EXPECT_THROW(p.request(STANDBY, true, false, false), std::runtime_error);
  p.request(STANDBY, true, false, true);
  EXPECT_EQ(p.mode, STANDBY);
  p.request(READY, true, false, false);
  p.stage = 2; EXPECT_EQ(p.state(), "PROCESSING");
  p.stage = 3; EXPECT_EQ(p.state(), "STORING");
  p.stage = 0; EXPECT_EQ(p.state(), "READY");
}
TEST(Modes, DownlinkInhibitsAcquisition) {
  ModePolicy p;
  p.request(STANDBY, false, false, false);
  p.request(DOWNLINK, false, false, false);
  EXPECT_EQ(p.state(), "DOWNLINK");
  EXPECT_FALSE(p.can_capture(true, true));
  p.request(READY, true, false, false);
  p.request(STANDBY, true, false, false);
  EXPECT_FALSE(p.can_capture(true, true));
}
