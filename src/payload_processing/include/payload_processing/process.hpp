#pragma once
#include <cstdint>
#include <vector>
#include "sensor_msgs/msg/image.hpp"

namespace payload_processing {
struct Options {
  unsigned crop_x{0}, crop_y{0}, crop_width{0}, crop_height{0};
  unsigned resize_width{0}, resize_height{0};
  bool grayscale{false};
  int quality{75};
};
struct Result {
  std::vector<uint8_t> jpeg;
  unsigned width{0}, height{0};
  double mean{0}, minimum{255}, maximum{0};
};
Result process(const sensor_msgs::msg::Image &image, const Options &options);
}  // namespace payload_processing
