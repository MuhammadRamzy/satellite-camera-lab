#include <gtest/gtest.h>
#include "payload_processing/process.hpp"

sensor_msgs::msg::Image fixture() {
  sensor_msgs::msg::Image image;
  image.width = 2; image.height = 2; image.step = 6; image.encoding = "rgb8";
  image.data = {255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255};
  return image;
}
TEST(Processing, ValidColorAndStatistics) {
  const auto result = payload_processing::process(fixture(), {});
  EXPECT_EQ(result.width, 2U); EXPECT_EQ(result.height, 2U);
  EXPECT_DOUBLE_EQ(result.mean, 127.5);
  EXPECT_DOUBLE_EQ(result.minimum, 0); EXPECT_DOUBLE_EQ(result.maximum, 255);
  ASSERT_GT(result.jpeg.size(), 4U);
  EXPECT_EQ(result.jpeg[0], 0xff); EXPECT_EQ(result.jpeg[1], 0xd8);
  EXPECT_EQ(result.jpeg[result.jpeg.size() - 2], 0xff);
  EXPECT_EQ(result.jpeg.back(), 0xd9);
}
TEST(Processing, CropResizeGrayscale) {
  payload_processing::Options options;
  options.crop_x = 1; options.crop_width = 1; options.crop_height = 1;
  options.resize_width = 7; options.resize_height = 3; options.grayscale = true;
  const auto result = payload_processing::process(fixture(), options);
  EXPECT_EQ(result.width, 7U); EXPECT_EQ(result.height, 3U);
  EXPECT_DOUBLE_EQ(result.mean, 149); EXPECT_DOUBLE_EQ(result.minimum, 149);
  EXPECT_DOUBLE_EQ(result.maximum, 149);
}
TEST(Processing, PaddedStrideAccepted) {
  auto image = fixture();
  image.step = 8;
  image.data = {255, 0, 0, 0, 255, 0, 99, 99, 0, 0, 255, 255, 255, 255, 99, 99};
  EXPECT_DOUBLE_EQ(payload_processing::process(image, {}).mean, 127.5);
}
TEST(Processing, InvalidEncodingAndBufferRejected) {
  auto image = fixture();
  image.encoding = "bgr8";
  EXPECT_THROW(payload_processing::process(image, {}), std::invalid_argument);
  image = fixture(); image.data.pop_back();
  EXPECT_THROW(payload_processing::process(image, {}), std::invalid_argument);
  image = fixture(); image.step = 1;
  EXPECT_THROW(payload_processing::process(image, {}), std::invalid_argument);
}
TEST(Processing, BoundsAndQualityRejected) {
  const auto image = fixture();
  payload_processing::Options options;
  options.quality = 0;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
  options.quality = 101;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
  options = {}; options.crop_x = 2;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
  options = {}; options.crop_width = 3;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
  options = {}; options.resize_width = 3;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
  options.resize_height = 1081;
  EXPECT_THROW(payload_processing::process(image, options), std::invalid_argument);
}
