#include <chrono>
#include <memory>
#include <stdexcept>
#include "rclcpp/rclcpp.hpp"
#include "payload_interfaces/msg/capture.hpp"
#include "payload_interfaces/msg/compressed_capture.hpp"
#include "payload_interfaces/msg/file_ready.hpp"
#include "payload_interfaces/msg/node_health.hpp"
#include "payload_interfaces/msg/stage_event.hpp"
#include "payload_processing/process.hpp"

class Processing final : public rclcpp::Node {
public:
  Processing() : Node("payload_processing") {
    declare_parameter("grayscale", false);
    for (const auto &name : {"crop_x", "crop_y", "crop_width", "crop_height", "resize_width", "resize_height"}) {
      declare_parameter(name, 0);
    }
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable().durability_volatile();
    pub_ = create_publisher<payload_interfaces::msg::CompressedCapture>("/payload/compressed", qos);
    errors_ = create_publisher<payload_interfaces::msg::FileReady>("/payload/file_ready", 16);
    stages_ = create_publisher<payload_interfaces::msg::StageEvent>("/payload/stage", 16);
    health_ = create_publisher<payload_interfaces::msg::NodeHealth>("/payload/node_health", 16);
    heartbeat_ = create_wall_timer(std::chrono::seconds(1), [this]() {
      payload_interfaces::msg::NodeHealth msg;
      msg.node_name = "payload_processing"; msg.sequence = ++sequence_;
      msg.healthy = true;
      try {
        for (const auto &name : {"crop_x", "crop_y", "crop_width", "crop_height", "resize_width", "resize_height"}) {
          dimension(name);
        }
        if (dimension("resize_height") > 1080 ||
            ((dimension("resize_width") == 0) != (dimension("resize_height") == 0))) {
          throw std::invalid_argument("invalid resize configuration");
        }
      } catch (const std::exception &error) {
        msg.healthy = false; last_error_ = error.what();
      }
      msg.detail = last_error_.substr(0, 256); health_->publish(msg);
    });
    sub_ = create_subscription<payload_interfaces::msg::Capture>("/payload/capture", qos,
      [this](const payload_interfaces::msg::Capture &input) {
        const auto start = std::chrono::steady_clock::now();
        payload_interfaces::msg::StageEvent stage;
        stage.capture_id = input.metadata.capture_id; stage.stage = stage.PROCESSING;
        stages_->publish(stage);
        try {
          if (input.metadata.width != input.image.width || input.metadata.height != input.image.height) {
            throw std::invalid_argument("metadata and image dimensions differ");
          }
          if (pub_->get_subscription_count() == 0) { throw std::runtime_error("storage subscriber unavailable"); }
          payload_processing::Options options;
          options.quality = input.metadata.quality;
          options.grayscale = get_parameter("grayscale").as_bool();
          options.crop_x = dimension("crop_x"); options.crop_y = dimension("crop_y");
          options.crop_width = dimension("crop_width"); options.crop_height = dimension("crop_height");
          options.resize_width = dimension("resize_width"); options.resize_height = dimension("resize_height");
          auto processed = payload_processing::process(input.image, options);
          payload_interfaces::msg::CompressedCapture output;
          output.metadata = input.metadata;
          output.metadata.width = processed.width; output.metadata.height = processed.height;
          output.metadata.encoding = options.grayscale ? "mono8/jpeg" : "rgb8/jpeg";
          output.metadata.compressed_bytes = processed.jpeg.size();
          output.metadata.mean = processed.mean; output.metadata.minimum = processed.minimum;
          output.metadata.maximum = processed.maximum;
          output.metadata.processing_ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - start).count();
          output.image.header = input.image.header;
          output.image.format = options.grayscale ? "mono8; jpeg compressed mono8" : "rgb8; jpeg compressed rgb8";
          output.image.data = std::move(processed.jpeg);
          pub_->publish(output);
          last_error_.clear();
          RCLCPP_INFO(get_logger(), "event=image_processed id=%s bytes=%zu processing_ms=%.3f",
            output.metadata.capture_id.c_str(), output.image.data.size(), output.metadata.processing_ms);
        } catch (const std::exception &error) {
          last_error_ = error.what();
          payload_interfaces::msg::FileReady event;
          event.metadata = input.metadata;
          event.error = std::string(error.what()).substr(0, 256);
          errors_->publish(event);
          RCLCPP_ERROR(get_logger(), "event=processing_failed id=%s reason=%s", input.metadata.capture_id.c_str(), error.what());
        }
      });
  }
private:
  unsigned dimension(const char *name) {
    const auto value = get_parameter(name).as_int();
    if (value < 0 || value > 1920) { throw std::invalid_argument("processing dimension outside bounds"); }
    return static_cast<unsigned>(value);
  }
  rclcpp::Publisher<payload_interfaces::msg::CompressedCapture>::SharedPtr pub_;
  rclcpp::Publisher<payload_interfaces::msg::FileReady>::SharedPtr errors_;
  rclcpp::Subscription<payload_interfaces::msg::Capture>::SharedPtr sub_;
  rclcpp::Publisher<payload_interfaces::msg::StageEvent>::SharedPtr stages_;
  rclcpp::Publisher<payload_interfaces::msg::NodeHealth>::SharedPtr health_;
  rclcpp::TimerBase::SharedPtr heartbeat_;
  uint64_t sequence_{0};
  std::string last_error_;
};
int main(int argc, char **argv) {
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<Processing>());
  rclcpp::shutdown();
}
