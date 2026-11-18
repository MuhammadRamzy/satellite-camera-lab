#include <chrono>
#include <cstdint>
#include <memory>
#include <string>
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/u_int32.hpp"

// Distinct processes and volatile QoS exercise actual DDS delivery, not a
// publisher's local callback. External timeout supplies a non-zero failure exit.
class Probe final : public rclcpp::Node {
public:
  explicit Probe(bool receiver) : Node(receiver ? "smoke_receiver" : "smoke_sender") {
    auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable().durability_volatile();
    if (receiver) {
      sub_ = create_subscription<std_msgs::msg::UInt32>(
        "/payload/smoke", qos, [this](const std_msgs::msg::UInt32 &msg) {
          if (msg.data > last_) {
            last_ = msg.data;
            if (++received_ == 5) {
              RCLCPP_INFO(get_logger(), "SMOKE_PASS received=5 last=%u", last_);
              rclcpp::shutdown();
            }
          }
        });
    } else {
      pub_ = create_publisher<std_msgs::msg::UInt32>("/payload/smoke", qos);
      timer_ = create_wall_timer(std::chrono::milliseconds(100), [this]() {
        std_msgs::msg::UInt32 msg;
        msg.data = ++last_;
        pub_->publish(msg);
      });
    }
  }
private:
  uint32_t last_{0};
  unsigned received_{0};
  rclcpp::Publisher<std_msgs::msg::UInt32>::SharedPtr pub_;
  rclcpp::Subscription<std_msgs::msg::UInt32>::SharedPtr sub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char **argv) {
  rclcpp::init(argc, argv);
  const bool receiver = argc > 1 && std::string(argv[1]) == "receive";
  rclcpp::spin(std::make_shared<Probe>(receiver));
  if (rclcpp::ok()) { rclcpp::shutdown(); }
  return 0;
}
