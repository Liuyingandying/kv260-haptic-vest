#ifndef BASIC_DEPTH_V1_HPP
#define BASIC_DEPTH_V1_HPP

#include <array>
#include <cstdint>
#include <istream>
#include <string>
#include <vector>

namespace basic_depth {

// This independent V1 does not replace or claim equivalence to V2.2.
struct Config {
    double measurement_min_m = 0.1;
    double measurement_max_m = 6.0;
    double connect_abs_m = 0.08;
    double connect_rel = 0.12;
    unsigned surface_min_pixels = 4;
    double percentile = 10.0;
    double complete_ratio = 0.9;
    double quantize_min_m = 0.3;
    double attention_max_m = 2.5;
    double danger_m = 0.8;
    double caution_m = 1.5;
    double stale_ms = 250.0;
    double future_tolerance_ms = 40.0;
};

struct Candidate {
    bool present = false;
    double distance_m = 0.0;
    unsigned u = 0, v = 0;
    unsigned support = 0, region_pixels = 0;
};

struct Region {
    unsigned area = 0, accepted_pixels = 0;
    double accepted_ratio = 0.0;
    std::string quality = "UNKNOWN";
    std::string risk = "UNKNOWN";
    Candidate nearest;
};

struct SpatialResult {
    unsigned width = 0, height = 0, roi_x = 0, roi_y = 0, roi_size = 0;
    unsigned accepted_pixels = 0, supported_pixels = 0, component_count = 0;
    unsigned invalid_pixels = 0, outside_model_pixels = 0;
    unsigned outside_attention_pixels = 0, credible_component_count = 0;
    unsigned rejected_noise_pixels = 0;
    double invalid_ratio = 0.0;
    std::string quality = "UNKNOWN";
    double accepted_ratio = 0.0;
    std::array<unsigned char, 256> matrix{};
    std::array<Region, 256> cells{};
    Region full, center, left, right;
};

Config load_config(const std::string& path);
void validate_config(const Config& cfg);
unsigned char quantize(double distance_m, const Config& cfg);
SpatialResult analyze(const std::vector<float>& metres, unsigned width,
                      unsigned height, const Config& cfg);

}  // namespace basic_depth
#endif
