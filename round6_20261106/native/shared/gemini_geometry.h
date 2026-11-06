#pragma once
#include <stdint.h>

#if defined(_WIN32)
#define GF_API extern "C" __declspec(dllexport)
#else
#define GF_API extern "C" __attribute__((visibility("default")))
#endif

// ABI 1: ordinary C layout, no packing. Integers are int32, times int64 ns.
struct GFConfig {
    int32_t width, height, tile_size, halo_pixels, component_min_pixels;
    double depth_band_m, adjacency_max_step_m;
    double measurement_min_m, measurement_max_m, attention_max_m;
    double danger_m, caution_m, complete_ratio, quantize_min_m;
};

struct GFRegion {
    int32_t bounds[4];
    int32_t total_pixels, valid_pixels, coherent_pixels, unsupported_near_pixels;
    int32_t has_candidate, quality; // quality 0 UNKNOWN, 1 VALID, 2 SPARSE
    int32_t uv[2], component_support, region_support, level;
    int32_t witness_id, phase_index, risk_zone, method; // method 1 BAND, 2 ADJACENCY
    int32_t tile_core_bounds[4], witness_bounds[4];
    double coverage, coherent_coverage, distance_m;
    double depth_band_low_m, depth_band_high_m;
    double actual_depth_min_m, actual_depth_max_m;
    double adjacency_max_step_m;
};

struct GFCounts {
    int32_t coherent_pixels, adjacency_pixels, unsupported_near_pixels;
    int32_t witness_count, accepted_pixels;
};

struct GFStageTimes {
    int64_t build_witness_ns, adjacency_ns, region_ns;
    int64_t candidate_percentile_ns, mapping_ns, total_ns;
};

GF_API int gf_geometry_abi_version();
// Input depth_m and valid are row-major, contiguous width*height entries.
// Output regions are 256 row-major central cells, left side, right side.
// Only geometry; no timestamps, producer, protocol or output permission.
// Nonzero error is fail-closed; outputs are not valid on error.
GF_API int gf_geometry(const double* depth_m, const uint8_t* valid,
                       const GFConfig* config, GFRegion* regions_258,
                       GFCounts* counts, GFStageTimes* timings);
