#ifndef BASIC_DEPTH_PHASE_PROFILE_HPP
#define BASIC_DEPTH_PHASE_PROFILE_HPP
#include <chrono>
#include <cstdint>
#include <ostream>

namespace basic_depth_profile {
using Clock = std::chrono::steady_clock;
inline std::uint64_t now_ns() {
    return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}
inline double elapsed_ms(std::uint64_t start) { return static_cast<double>(now_ns() - start) / 1e6; }
struct FrameProfile {
    std::uint64_t read_start_ns = 0;
    double input_read_decode_ms = 0, input_read_wait_ms = 0, input_decode_validate_allocate_ms = 0;
    double input_decode_conversion_ms = 0;
    double algorithm_total_ms = 0, algorithm_validate_setup_ms = 0, connectivity_build_ms = 0;
    double component_grouping_ms = 0, region_candidate_and_local_quality_ms = 0;
    double final_quality_statistics_ms = 0, algorithm_teardown_and_instrumentation_residual_ms = 0;
    double freshness_check_ms = 0, analysis_dispatch_ms = 0, json_serialization_ms = 0, output_write_flush_ms = 0;
    double complete_frame_wall_ms = 0, complete_processing_without_input_wait_ms = 0;
};
inline thread_local FrameProfile frame{};
struct ReadScope {
    ReadScope() { frame = FrameProfile{}; frame.read_start_ns = now_ns(); }
    ~ReadScope() {
        frame.input_read_decode_ms = elapsed_ms(frame.read_start_ns);
        frame.input_decode_validate_allocate_ms = frame.input_read_decode_ms - frame.input_read_wait_ms;
    }
};
struct AlgorithmScope {
    std::uint64_t start;
    AlgorithmScope() : start(now_ns()) {}
    ~AlgorithmScope() {
        frame.algorithm_total_ms = elapsed_ms(start);
        frame.algorithm_teardown_and_instrumentation_residual_ms = frame.algorithm_total_ms -
            (frame.algorithm_validate_setup_ms + frame.connectivity_build_ms + frame.component_grouping_ms +
             frame.region_candidate_and_local_quality_ms + frame.final_quality_statistics_ms);
    }
};
inline void emit_fields(std::ostream& out) {
    out << ",\"phase_profile_schema\":\"basic_depth_v1_phase_profile_v1\"";
#define EMIT_FIELD(name) out << ",\"" #name "\":" << frame.name
    EMIT_FIELD(input_read_decode_ms); EMIT_FIELD(input_read_wait_ms); EMIT_FIELD(input_decode_validate_allocate_ms);
    EMIT_FIELD(input_decode_conversion_ms);
    EMIT_FIELD(freshness_check_ms); EMIT_FIELD(analysis_dispatch_ms); EMIT_FIELD(algorithm_total_ms);
    EMIT_FIELD(algorithm_validate_setup_ms); EMIT_FIELD(connectivity_build_ms); EMIT_FIELD(component_grouping_ms);
    EMIT_FIELD(region_candidate_and_local_quality_ms); EMIT_FIELD(final_quality_statistics_ms);
    EMIT_FIELD(algorithm_teardown_and_instrumentation_residual_ms); EMIT_FIELD(json_serialization_ms);
    EMIT_FIELD(output_write_flush_ms); EMIT_FIELD(complete_frame_wall_ms); EMIT_FIELD(complete_processing_without_input_wait_ms);
#undef EMIT_FIELD
}
} // namespace basic_depth_profile
#endif
