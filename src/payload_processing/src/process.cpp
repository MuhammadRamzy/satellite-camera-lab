#include "payload_processing/process.hpp"
#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <csetjmp>
#include <stdexcept>
#include <jpeglib.h>

namespace payload_processing {
namespace {
struct Error {
  jpeg_error_mgr manager;
  std::jmp_buf jump;
  char message[JMSG_LENGTH_MAX];
};
void jpeg_error(j_common_ptr ptr) {
  auto *error = reinterpret_cast<Error *>(ptr->err);
  (*ptr->err->format_message)(ptr, error->message);
  std::longjmp(error->jump, 1);
}
// The compressor state and output pointer live on the heap so their values
// remain defined on the libjpeg error longjmp path. All C resources are freed.
struct State {
  jpeg_compress_struct compressor{};
  Error error{};
  unsigned char *data{nullptr};
  unsigned long size{0};
};
std::vector<uint8_t> compress(const std::vector<uint8_t> &pixels,
                            unsigned width, unsigned height, bool gray, int quality) {
  auto *state = new State;
  state->compressor.err = jpeg_std_error(&state->error.manager);
  state->error.manager.error_exit = jpeg_error;
  if (setjmp(state->error.jump)) {
    const std::string message(state->error.message);
    jpeg_destroy_compress(&state->compressor);
    std::free(state->data);
    delete state;
    throw std::runtime_error("JPEG compression: " + message);
  }
  jpeg_create_compress(&state->compressor);
  jpeg_mem_dest(&state->compressor, &state->data, &state->size);
  state->compressor.image_width = width;
  state->compressor.image_height = height;
  state->compressor.input_components = gray ? 1 : 3;
  state->compressor.in_color_space = gray ? JCS_GRAYSCALE : JCS_RGB;
  jpeg_set_defaults(&state->compressor);
  jpeg_set_quality(&state->compressor, quality, TRUE);
  jpeg_start_compress(&state->compressor, TRUE);
  const size_t stride = static_cast<size_t>(width) * (gray ? 1U : 3U);
  while (state->compressor.next_scanline < height) {
    auto *row = const_cast<JSAMPLE *>(pixels.data() + state->compressor.next_scanline * stride);
    jpeg_write_scanlines(&state->compressor, &row, 1);
  }
  jpeg_finish_compress(&state->compressor);
  // Construct only after the C operations that can longjmp have finished.
  std::vector<uint8_t> output;
  try {
    output.assign(state->data, state->data + state->size);
  } catch (...) {
    jpeg_destroy_compress(&state->compressor);
    std::free(state->data);
    delete state;
    throw;
  }
  jpeg_destroy_compress(&state->compressor);
  std::free(state->data);
  delete state;
  return output;
}
}  // namespace

Result process(const sensor_msgs::msg::Image &image, const Options &o) {
  if (image.encoding != "rgb8" || image.width < 1 || image.height < 1 ||
      image.width > 1920 || image.height > 1080 ||
      image.step < image.width * 3 || image.step > 1920 * 3 + 256 ||
      image.data.size() != static_cast<size_t>(image.step) * image.height ||
      o.quality < 1 || o.quality > 100) {
    throw std::invalid_argument("invalid RGB image, stride, size or quality");
  }
  if (o.crop_x >= image.width || o.crop_y >= image.height) {
    throw std::invalid_argument("crop origin outside image");
  }
  const unsigned cw = o.crop_width ? o.crop_width : image.width - o.crop_x;
  const unsigned ch = o.crop_height ? o.crop_height : image.height - o.crop_y;
  if (cw > image.width - o.crop_x || ch > image.height - o.crop_y ||
      ((o.resize_width == 0) != (o.resize_height == 0))) {
    throw std::invalid_argument("invalid crop or incomplete resize dimensions");
  }
  Result result;
  result.width = o.resize_width ? o.resize_width : cw;
  result.height = o.resize_height ? o.resize_height : ch;
  if (result.width > 1920 || result.height > 1080) {
    throw std::invalid_argument("resize exceeds bounded image dimensions");
  }
  const unsigned channels = o.grayscale ? 1 : 3;
  std::vector<uint8_t> pixels(static_cast<size_t>(result.width) * result.height * channels);
  double sum = 0;
  for (unsigned y = 0; y < result.height; ++y) {
    for (unsigned x = 0; x < result.width; ++x) {
      const unsigned sx = o.crop_x + x * cw / result.width;
      const unsigned sy = o.crop_y + y * ch / result.height;
      const auto *src = image.data.data() + static_cast<size_t>(sy) * image.step + sx * 3;
      auto *dst = pixels.data() + (static_cast<size_t>(y) * result.width + x) * channels;
      if (o.grayscale) {
        dst[0] = static_cast<uint8_t>((77U * src[0] + 150U * src[1] + 29U * src[2]) >> 8);
      } else {
        std::copy(src, src + 3, dst);
      }
      for (unsigned c = 0; c < channels; ++c) {
        sum += dst[c];
        result.minimum = std::min(result.minimum, static_cast<double>(dst[c]));
        result.maximum = std::max(result.maximum, static_cast<double>(dst[c]));
      }
    }
  }
  result.mean = sum / pixels.size();
  result.jpeg = compress(pixels, result.width, result.height, o.grayscale, o.quality);
  return result;
}
}  // namespace payload_processing
