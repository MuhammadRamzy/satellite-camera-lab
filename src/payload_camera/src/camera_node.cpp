#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <memory>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include "rclcpp/rclcpp.hpp"
#include "rclcpp/create_publisher.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "lifecycle_msgs/msg/state.hpp"
#include "lifecycle_msgs/msg/transition.hpp"
#include "sensor_msgs/msg/camera_info.hpp"
#include "payload_interfaces/msg/authority.hpp"
#include "payload_interfaces/msg/camera_status.hpp"
#include "payload_interfaces/msg/stage_event.hpp"
#include "payload_interfaces/msg/capture.hpp"
#include "payload_interfaces/msg/file_ready.hpp"
#include "payload_interfaces/srv/capture.hpp"
#include "payload_interfaces/srv/apply_control.hpp"
#include "payload_interfaces/action/capture_burst.hpp"
#include "payload_camera/mode_policy.hpp"

using Clock = std::chrono::steady_clock;
using namespace payload_camera;
using LCState = lifecycle_msgs::msg::State;
using Transition = lifecycle_msgs::msg::Transition;
class Camera final : public rclcpp_lifecycle::LifecycleNode {
  using Return = rclcpp_lifecycle::node_interfaces::LifecycleNodeInterface::CallbackReturn;
  using Burst = payload_interfaces::action::CaptureBurst;
  using GoalHandle = rclcpp_action::ServerGoalHandle<Burst>;
public:
  Camera() : LifecycleNode("payload_camera"), ids_(std::random_device{}()), boot_(Clock::now()) {
    boot_id_ = uuid();
    declare_parameter("width", 640); declare_parameter("height", 480);
    declare_parameter("quality", 75); declare_parameter("exposure_us", 10000);
    declare_parameter("noise_sigma", 2.0); declare_parameter("noise_seed", 42);
    declare_parameter("authority_timeout_s", 1.5); declare_parameter("pipeline_timeout_s", 30.0);
    const auto image_qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable().durability_volatile();
    capture_pub_ = create_publisher<payload_interfaces::msg::Capture>("/payload/capture", image_qos);
    image_pub_ = create_publisher<sensor_msgs::msg::Image>("/payload/image_raw", image_qos);
    info_pub_ = create_publisher<sensor_msgs::msg::CameraInfo>("/payload/camera_info", image_qos);
    status_pub_ = rclcpp::create_publisher<payload_interfaces::msg::CameraStatus>(
      *this, "/payload/camera_status", rclcpp::QoS(32).reliable().transient_local());
    authority_sub_ = create_subscription<payload_interfaces::msg::Authority>("/payload/authority", 1,
      [this](const payload_interfaces::msg::Authority &msg) {
        if (msg.boot_id.empty()) { return; }
        if (msg.boot_id == authority_id_ && msg.sequence <= authority_sequence_) { return; }
        if (msg.boot_id != authority_id_ && has_lease_ && lease_fresh()) { return; }
        if (has_lease_ && !lease_fresh() && policy_.mode == READY) {
          inhibit(true, "authority lease expired");
        }
        authority_id_ = msg.boot_id; authority_sequence_ = msg.sequence;
        last_authority_ = Clock::now(); has_lease_ = true;
        imaging_permitted_ = msg.imaging_permitted && !msg.force_safe && !msg.fault;
        if (msg.fault) { inhibit(true, "supervisor detected missing or failed node"); }
        else if (msg.force_safe || (!msg.imaging_permitted && policy_.mode == READY)) {
          inhibit(false, "spacecraft SAFE or imaging inhibited");
        }
      });
    stage_sub_ = create_subscription<payload_interfaces::msg::StageEvent>("/payload/stage", 16,
      [this](const payload_interfaces::msg::StageEvent &event) {
        if (event.capture_id == in_flight_ && event.stage <= 3) { advance(event.stage); }
      });
    done_sub_ = create_subscription<payload_interfaces::msg::FileReady>("/payload/file_ready", 16,
      [this](const payload_interfaces::msg::FileReady &event) {
        if (event.metadata.capture_id != in_flight_ || in_flight_.empty()) { return; }
        const auto finished_id = in_flight_;
        if (event.success) { advance(3); }
        in_flight_.clear(); policy_.stage = 0;
        if (!event.success) { inhibit(true, "pipeline failure: " + event.error); }
        if (burst_ && event.success) {
          ++completed_;
          auto feedback = std::make_shared<Burst::Feedback>();
          feedback->completed = completed_; feedback->requested = burst_->get_goal()->count;
          feedback->last_capture_id = finished_id;
          burst_->publish_feedback(feedback);
        }
        publish_status();
      });
    capture_service_ = create_service<payload_interfaces::srv::Capture>("/payload/capture_single",
      [this](const std::shared_ptr<payload_interfaces::srv::Capture::Request>,
             std::shared_ptr<payload_interfaces::srv::Capture::Response> response) {
        try {
          if (burst_ || goal_reserved_) { throw std::runtime_error("burst active"); }
          response->capture_id = acquire(); response->accepted = true;
        } catch (const std::exception &error) {
          response->error = std::string(error.what()).substr(0, 256);
          RCLCPP_ERROR(get_logger(), "event=capture_rejected reason=%s", error.what());
        }
      });
    control_service_ = create_service<payload_interfaces::srv::ApplyControl>("/payload/apply_control",
      [this](const std::shared_ptr<payload_interfaces::srv::ApplyControl::Request> request,
             std::shared_ptr<payload_interfaces::srv::ApplyControl::Response> response) {
        try {
          if (!lease_fresh() || request->authority_id != authority_id_) {
            throw std::runtime_error("controller does not own a fresh authority lease");
          }
          if (authority_sequence_ < request->authority_sequence) {
            throw std::runtime_error("authority sequence boundary not yet observed");
          }
          policy_.request(request->mode, permitted(), busy(), request->recover);
          if (policy_.mode != READY && burst_) { abort_reason_ = "mode changed; burst inhibited"; }
          synchronize_lifecycle();
          response->accepted = true;
        } catch (const std::exception &error) {
          response->reason = std::string(error.what()).substr(0, 256);
        }
        publish_status(); response->status = status();
      });
    burst_server_ = rclcpp_action::create_server<Burst>(this, "/payload/capture_burst",
      [this](const rclcpp_action::GoalUUID &, std::shared_ptr<const Burst::Goal> goal) {
        if (!allowed() || busy() || goal->count < 1 || goal->count > 100 ||
            goal->interval_ms < 100 || goal->interval_ms > 60000) {
          return rclcpp_action::GoalResponse::REJECT;
        }
        try { validate_config(); } catch (const std::exception &) { return rclcpp_action::GoalResponse::REJECT; }
        goal_reserved_ = true;
        return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },
      [this](std::shared_ptr<GoalHandle> goal) {
        return goal == burst_ ? rclcpp_action::CancelResponse::ACCEPT : rclcpp_action::CancelResponse::REJECT;
      },
      [this](std::shared_ptr<GoalHandle> goal) {
        burst_ = goal; goal_reserved_ = false; completed_ = 0;
        abort_reason_.clear(); next_capture_ = Clock::now(); publish_status();
      });
    work_timer_ = create_wall_timer(std::chrono::milliseconds(20), [this]() { tick(); });
    status_timer_ = create_wall_timer(std::chrono::milliseconds(200), [this]() { publish_status(); });
  }
private:
  bool lease_fresh() const {
    return has_lease_ && std::chrono::duration<double>(Clock::now() - last_authority_).count() <
      get_parameter("authority_timeout_s").as_double();
  }
  bool permitted() const { return imaging_permitted_ && lease_fresh(); }
  bool busy() const { return !in_flight_.empty() || static_cast<bool>(burst_) || goal_reserved_; }
  bool allowed() const {
    return policy_.can_capture(permitted(), get_current_state().id() == LCState::PRIMARY_STATE_ACTIVE) &&
      capture_pub_->get_subscription_count() > 0;
  }
  void validate_config() const {
    const auto w = get_parameter("width").as_int(), h = get_parameter("height").as_int();
    const auto quality = get_parameter("quality").as_int(), exposure = get_parameter("exposure_us").as_int();
    const auto sigma = get_parameter("noise_sigma").as_double();
    const auto lease = get_parameter("authority_timeout_s").as_double();
    const auto timeout = get_parameter("pipeline_timeout_s").as_double();
    if (w < 16 || w > 1920 || h < 16 || h > 1080 || quality < 1 || quality > 100 ||
        exposure < 1 || exposure > 1000000 || !std::isfinite(sigma) || sigma < 0 || sigma > 100 ||
        !std::isfinite(lease) || lease < 0.5 || lease > 5 || !std::isfinite(timeout) || timeout < 1 || timeout > 120) {
      throw std::invalid_argument("camera configuration outside experiment bounds");
    }
  }
  Return on_configure(const rclcpp_lifecycle::State &) override {
    try { validate_config(); } catch (const std::exception &error) {
      policy_.inhibit(true, error.what()); return Return::FAILURE;
    }
    if (policy_.mode == BOOT) { policy_.request(STANDBY, false, false, false); }
    return Return::SUCCESS;
  }
  Return on_activate(const rclcpp_lifecycle::State &) override {
    try { validate_config(); } catch (const std::exception &error) {
      policy_.inhibit(true, error.what()); return Return::FAILURE;
    }
    if (policy_.mode != READY || !permitted() || capture_pub_->get_subscription_count() == 0) {
      return Return::FAILURE;
    }
    capture_pub_->on_activate(); image_pub_->on_activate(); info_pub_->on_activate();
    return Return::SUCCESS;
  }
  Return on_deactivate(const rclcpp_lifecycle::State &) override {
    capture_pub_->on_deactivate(); image_pub_->on_deactivate(); info_pub_->on_deactivate();
    if (policy_.mode == READY) { policy_.inhibit(false, "external lifecycle deactivation"); }
    if (burst_) { abort_reason_ = "camera deactivated"; }
    return Return::SUCCESS;
  }
  Return on_cleanup(const rclcpp_lifecycle::State &) override {
    if (busy()) { return Return::FAILURE; }
    policy_ = ModePolicy{}; return Return::SUCCESS;
  }
  Return on_shutdown(const rclcpp_lifecycle::State &state) override {
    policy_.inhibit(false, "camera shutdown"); return on_deactivate(state);
  }
  void synchronize_lifecycle() {
    if (get_current_state().id() == LCState::PRIMARY_STATE_UNCONFIGURED) {
      trigger_transition(Transition::TRANSITION_CONFIGURE);
      if (get_current_state().id() != LCState::PRIMARY_STATE_INACTIVE) {
        policy_.inhibit(true, "lifecycle configure failed"); throw std::runtime_error(policy_.reason);
      }
    }
    if (policy_.mode != READY && get_current_state().id() == LCState::PRIMARY_STATE_ACTIVE) {
      trigger_transition(Transition::TRANSITION_DEACTIVATE);
    }
    if (policy_.mode == READY && get_current_state().id() == LCState::PRIMARY_STATE_INACTIVE) {
      trigger_transition(Transition::TRANSITION_ACTIVATE);
      if (get_current_state().id() != LCState::PRIMARY_STATE_ACTIVE) {
        policy_.inhibit(true, "lifecycle activation failed"); throw std::runtime_error(policy_.reason);
      }
    }
    const auto expected = policy_.mode == READY ? LCState::PRIMARY_STATE_ACTIVE : LCState::PRIMARY_STATE_INACTIVE;
    if (get_current_state().id() != expected) {
      policy_.inhibit(true, "unexpected lifecycle state"); throw std::runtime_error(policy_.reason);
    }
  }
  void inhibit(bool fault, const std::string &why) {
    policy_.inhibit(fault, why.substr(0, 256));
    imaging_permitted_ = false;
    if (burst_) { abort_reason_ = why.substr(0, 256); }
    if (get_current_state().id() == LCState::PRIMARY_STATE_ACTIVE) {
      trigger_transition(Transition::TRANSITION_DEACTIVATE);
    }
    publish_status();
  }
  void advance(unsigned target) {
    while (policy_.stage < target) { ++policy_.stage; publish_status(); }
  }
  std::string acquire() {
    if (!allowed() || !in_flight_.empty()) { throw std::runtime_error("camera inhibited or pipeline busy"); }
    validate_config();
    policy_.stage = 1; publish_status();
    try {
      auto capture = generate();
      in_flight_ = capture.metadata.capture_id; started_ = Clock::now();
      capture_pub_->publish(capture); image_pub_->publish(capture.image);
      sensor_msgs::msg::CameraInfo info;
      info.header = capture.image.header; info.width = capture.image.width; info.height = capture.image.height;
      info_pub_->publish(info); publish_status();
      return in_flight_;
    } catch (...) {
      policy_.stage = 0;
      inhibit(true, "image acquisition failed");
      throw;
    }
  }
  payload_interfaces::msg::CameraStatus status() {
    payload_interfaces::msg::CameraStatus msg;
    msg.boot_id = boot_id_; msg.mode = policy_.mode; msg.operational_state = policy_.state();
    msg.lifecycle_state = get_current_state().id(); msg.in_flight = in_flight_;
    msg.burst_active = static_cast<bool>(burst_) || goal_reserved_; msg.burst_completed = completed_;
    msg.imaging_enabled = policy_.mode == READY && permitted() && msg.lifecycle_state == LCState::PRIMARY_STATE_ACTIVE;
    msg.reason = policy_.reason; msg.sequence = ++status_sequence_;
    return msg;
  }
  void publish_status() { status_pub_->publish(status()); }
  void finish_burst(bool success, bool cancel) {
    auto result = std::make_shared<Burst::Result>();
    result->success = success; result->completed = completed_;
    result->reason = success ? "all images durably stored" : cancel ? "operator cancellation; in-flight write drained" : abort_reason_;
    if (success) { burst_->succeed(result); }
    else if (cancel) { burst_->canceled(result); }
    else { burst_->abort(result); }
    burst_.reset(); abort_reason_.clear(); publish_status();
  }
  void tick() {
    if (policy_.mode == READY && !lease_fresh()) { inhibit(true, "authority lease expired"); }
    if (!in_flight_.empty() && std::chrono::duration<double>(Clock::now() - started_).count() >
        get_parameter("pipeline_timeout_s").as_double()) {
      inhibit(true, "pipeline completion timeout; capture unresolved");
      if (burst_) { finish_burst(false, false); }
    }
    if (!burst_ || !in_flight_.empty()) { return; }
    if (!abort_reason_.empty() || policy_.mode != READY) { finish_burst(false, false); return; }
    if (burst_->is_canceling()) { finish_burst(false, true); return; }
    if (completed_ == burst_->get_goal()->count) { finish_burst(true, false); return; }
    if (Clock::now() >= next_capture_) {
      try {
        acquire();
        next_capture_ = started_ + std::chrono::milliseconds(burst_->get_goal()->interval_ms);
      } catch (const std::exception &error) {
        abort_reason_ = std::string(error.what()).substr(0, 256);
        inhibit(true, abort_reason_); finish_burst(false, false);
      }
    }
  }
  std::string uuid() {
    uint8_t bytes[16];
    for (auto &byte : bytes) { byte = static_cast<uint8_t>(ids_()); }
    bytes[6] = (bytes[6] & 0x0FU) | 0x40U;
    bytes[8] = (bytes[8] & 0x3FU) | 0x80U;
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (unsigned i = 0; i < 16; ++i) {
      if (i == 4 || i == 6 || i == 8 || i == 10) { out << '-'; }
      out << std::setw(2) << static_cast<unsigned>(bytes[i]);
    }
    return out.str();
  }
  payload_interfaces::msg::Capture generate() {
    const auto begin = Clock::now();
    const int64_t w = get_parameter("width").as_int(), h = get_parameter("height").as_int();
    const int64_t quality = get_parameter("quality").as_int();
    const int64_t exposure = get_parameter("exposure_us").as_int();
    const double sigma = get_parameter("noise_sigma").as_double();
    if (w < 16 || w > 1920 || h < 16 || h > 1080 || quality < 1 || quality > 100 ||
        exposure < 1 || exposure > 1000000 || !std::isfinite(sigma) || sigma < 0 || sigma > 100) {
      throw std::invalid_argument("camera configuration outside experiment bounds");
    }
    payload_interfaces::msg::Capture capture;
    auto &image = capture.image;
    auto &meta = capture.metadata;
    image.header.stamp = now();
    image.header.frame_id = "camera_optical_frame";
    image.width = static_cast<uint32_t>(w);
    image.height = static_cast<uint32_t>(h);
    image.encoding = "rgb8";
    image.step = image.width * 3;
    image.data.resize(static_cast<size_t>(image.step) * image.height);
    meta.capture_id = uuid();
    meta.utc = image.header.stamp;
    meta.spacecraft_time_us = std::chrono::duration_cast<std::chrono::microseconds>(begin - boot_).count();
    meta.source_width = meta.width = image.width;
    meta.source_height = meta.height = image.height;
    meta.quality = static_cast<uint8_t>(quality);
    meta.exposure_us = static_cast<uint32_t>(exposure);
    meta.noise_sigma = sigma;
    meta.raw_bytes = image.data.size();
    meta.encoding = "rgb8";
    // Fixed per-capture noise seed enables controlled compression comparisons.
    std::mt19937 noise(static_cast<uint32_t>(get_parameter("noise_seed").as_int()));
    std::normal_distribution<double> normal(0, sigma > 0 ? sigma : 1.0);
    for (int64_t y = 0; y < h; ++y) {
      for (int64_t x = 0; x < w; ++x) {
        const double nx = (2.0 * x - w) / h, ny = (2.0 * y - h) / h;
        const double radius = nx * nx + ny * ny;
        const bool earth = radius < 0.7;
        const double land = std::sin(nx * 13) * std::cos(ny * 19) + std::sin((nx + ny) * 21);
        int colors[3] = {earth ? (land > 0.4 ? 70 : 15) : 3,
                         earth ? (land > 0.4 ? 135 : 70) : 5,
                         earth ? (land > 0.4 ? 65 : 170) : 14};
        if (!earth && (x * 7919 + y * 104729) % 997 == 0) {
          colors[0] = colors[1] = colors[2] = 230;
        }
        for (unsigned c = 0; c < 3; ++c) {
          const double value = colors[c] + (sigma > 0 ? normal(noise) : 0);
          image.data[(y * w + x) * 3 + c] = static_cast<uint8_t>(std::clamp(value, 0.0, 255.0));
        }
      }
    }
    meta.capture_ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
    return capture;
  }
  ModePolicy policy_;
  std::mt19937_64 ids_;
  Clock::time_point boot_, started_, next_capture_, last_authority_;
  std::string boot_id_, in_flight_, authority_id_, abort_reason_;
  uint64_t authority_sequence_{0}, status_sequence_{0};
  bool has_lease_{false}, imaging_permitted_{false}, goal_reserved_{false};
  uint32_t completed_{0};
  std::shared_ptr<GoalHandle> burst_;
  rclcpp_action::Server<Burst>::SharedPtr burst_server_;
  rclcpp_lifecycle::LifecyclePublisher<payload_interfaces::msg::Capture>::SharedPtr capture_pub_;
  rclcpp_lifecycle::LifecyclePublisher<sensor_msgs::msg::Image>::SharedPtr image_pub_;
  rclcpp_lifecycle::LifecyclePublisher<sensor_msgs::msg::CameraInfo>::SharedPtr info_pub_;
  rclcpp::Publisher<payload_interfaces::msg::CameraStatus>::SharedPtr status_pub_;
  rclcpp::Subscription<payload_interfaces::msg::Authority>::SharedPtr authority_sub_;
  rclcpp::Subscription<payload_interfaces::msg::StageEvent>::SharedPtr stage_sub_;
  rclcpp::Subscription<payload_interfaces::msg::FileReady>::SharedPtr done_sub_;
  rclcpp::Service<payload_interfaces::srv::Capture>::SharedPtr capture_service_;
  rclcpp::Service<payload_interfaces::srv::ApplyControl>::SharedPtr control_service_;
  rclcpp::TimerBase::SharedPtr work_timer_, status_timer_;
};
int main(int argc, char **argv) {
  rclcpp::init(argc, argv);
  auto camera = std::make_shared<Camera>();
  rclcpp::spin(camera->get_node_base_interface());
  rclcpp::shutdown();
}
