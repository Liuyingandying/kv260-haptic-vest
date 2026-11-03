#include "basic_depth.hpp"
#include "phase_profile.hpp"

#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace bd = basic_depth;
namespace {
struct Frame {
    unsigned width = 0, height = 0, stride = 0, kind = 0, session = 0, coordinate = 0;
    std::uint64_t seq = 0, capture_ns = 0, processing_ns = 0;
    double scale = 0;
    bool gap = false;
    std::vector<float> depth;
};
std::uint32_t u32(const unsigned char* p) {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
std::uint64_t u64(const unsigned char* p) { return std::uint64_t(u32(p)) | (std::uint64_t(u32(p + 4)) << 32); }
bool read_frame(std::istream& in, Frame& f) {
    basic_depth_profile::ReadScope read_scope;
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "IEEE754 binary32 required");
    static_assert(sizeof(double) == 8 && std::numeric_limits<double>::is_iec559, "IEEE754 binary64 required");
    unsigned char h[64];
    auto profile_read_start = basic_depth_profile::now_ns();
    in.read(reinterpret_cast<char*>(h), 64);
    basic_depth_profile::frame.input_read_wait_ms += basic_depth_profile::elapsed_ms(profile_read_start);
    if (in.gcount() == 0 && in.eof()) return false;
    if (in.gcount() != 64) throw std::runtime_error("truncated BDP1 header");
    if (std::memcmp(h, "BDP1", 4) != 0) throw std::runtime_error("invalid BDP1 magic");
    f = Frame{};
    f.width = u32(h + 4); f.height = u32(h + 8); f.stride = u32(h + 12);
    f.kind = u32(h + 16); f.session = u32(h + 20); f.seq = u64(h + 24);
    f.capture_ns = u64(h + 32); f.processing_ns = u64(h + 40);
    const std::uint64_t scale_bits = u64(h + 48);
    std::memcpy(&f.scale, &scale_bits, 8);
    f.coordinate = u32(h + 56);
    if (u32(h + 60) != 0) throw std::runtime_error("BDP1 reserved field must be zero");
    if (f.kind != 1 && f.kind != 2) throw std::runtime_error("unsupported BDP1 kind");
    if (f.coordinate != 1 && f.coordinate != 2) throw std::runtime_error("unsupported BDP1 coordinate");
    if (!std::isfinite(f.scale) || f.scale <= 0 || (f.kind == 1 && f.scale != 1.0))
        throw std::runtime_error("invalid BDP1 scale (F32_METRES requires 1)");
    if (f.width == 0 && f.height == 0 && f.stride == 0) { f.gap = true; return true; }
    const unsigned bytes_per_pixel = f.kind == 1 ? 4 : 2;
    if (f.width < 16 || f.width > 4096 || f.height < 16 || f.height > 4096 ||
        f.stride > 65536 || f.stride < f.width * bytes_per_pixel)
        throw std::runtime_error("BDP1 dimensions or stride invalid");
    const std::uint64_t payload_bytes = std::uint64_t(f.stride) * f.height;
    const std::uint64_t pixels = std::uint64_t(f.width) * f.height;
    if (payload_bytes > std::numeric_limits<std::size_t>::max() || pixels > std::numeric_limits<std::size_t>::max() / sizeof(float))
        throw std::runtime_error("BDP1 allocation overflow");
    std::vector<unsigned char> payload(static_cast<std::size_t>(payload_bytes));
    profile_read_start = basic_depth_profile::now_ns();
    in.read(reinterpret_cast<char*>(payload.data()), static_cast<std::streamsize>(payload.size()));
    basic_depth_profile::frame.input_read_wait_ms += basic_depth_profile::elapsed_ms(profile_read_start);
    if (static_cast<std::size_t>(in.gcount()) != payload.size()) throw std::runtime_error("truncated BDP1 payload");
    f.depth.resize(static_cast<std::size_t>(pixels));
    const auto conversion_start = basic_depth_profile::now_ns();
    for (unsigned y = 0; y < f.height; ++y) for (unsigned x = 0; x < f.width; ++x) {
        const unsigned char* p = payload.data() + static_cast<std::size_t>(y) * f.stride + x * bytes_per_pixel;
        float z = 0;
        if (f.kind == 1) {
            const std::uint32_t bits = u32(p);
            std::memcpy(&z, &bits, 4);
        } else {
            const unsigned raw = unsigned(p[0]) | (unsigned(p[1]) << 8);
            z = static_cast<float>(static_cast<double>(raw) * f.scale);
        }
        f.depth[static_cast<std::size_t>(y) * f.width + x] = z;
    }
    basic_depth_profile::frame.input_decode_conversion_ms = basic_depth_profile::elapsed_ms(conversion_start);
    return true;
}
double age_ms(const Frame& f) {
    return f.processing_ns >= f.capture_ns ? static_cast<double>(f.processing_ns - f.capture_ns) / 1e6 :
        -static_cast<double>(f.capture_ns - f.processing_ns) / 1e6;
}
struct Tracker {
    std::optional<unsigned> current;
    std::set<unsigned> retired;
    std::set<unsigned> disconnected;
    std::uint64_t last_seq = 0, max_capture = 0;
    std::string check(const Frame& f, const bd::Config& cfg) {
        if (f.gap) { disconnected.insert(f.session); return "STREAM_GAP"; }
        if (retired.count(f.session)) return "RETIRED_SESSION";
        if (disconnected.count(f.session)) return "STREAM_RECOVERY_REQUIRES_NEW_SESSION";
        if (!current || *current != f.session) {
            if (current) retired.insert(*current);
            current = f.session; last_seq = f.seq; max_capture = f.capture_ns;
        } else {
            if (f.seq == last_seq) return "DUPLICATE_FRAME";
            if (f.seq < last_seq) return "OUT_OF_ORDER_FRAME";
            last_seq = f.seq;
            if (f.capture_ns < max_capture) return "CLOCK_REGRESSION";
            max_capture = f.capture_ns;
        }
        const double age = age_ms(f);
        if (age < -cfg.future_tolerance_ms) return "FUTURE_FRAME";
        if (age > cfg.stale_ms) return "STALE_FRAME";
        return "OK";
    }
};
bd::SpatialResult unavailable(const Frame& f, const bd::Config& cfg) {
    bd::SpatialResult r;
    r.width = f.width; r.height = f.height;
    if (f.gap) return r;
    r.roi_size = std::min(f.width, f.height);
    r.roi_x = f.width / 2 - r.roi_size / 2; r.roi_y = f.height / 2 - r.roi_size / 2;
    for (float z : f.depth) {
        if (!std::isfinite(z) || z <= 0) ++r.invalid_pixels;
        else if (static_cast<double>(z) < cfg.measurement_min_m || static_cast<double>(z) > cfg.measurement_max_m) ++r.outside_model_pixels;
        else {
            ++r.accepted_pixels;
            if (static_cast<double>(z) >= cfg.attention_max_m) ++r.outside_attention_pixels;
        }
    }
    r.accepted_ratio = static_cast<double>(r.accepted_pixels) / f.depth.size();
    r.invalid_ratio = static_cast<double>(r.invalid_pixels) / f.depth.size();
    return r;
}
void candidate_json(std::ostream& out, const bd::Candidate& c) {
    out << "\"distance_m\":";
    if (c.present) out << c.distance_m; else out << "null";
    out << ",\"uv\":";
    if (c.present) out << '[' << c.u << ',' << c.v << ']'; else out << "null";
    out << ",\"component_support\":" << (c.present ? c.support : 0)
        << ",\"region_support\":" << (c.present ? c.region_pixels : 0);
}
void region_json(std::ostream& out, const bd::Region& r, bool cell,
                 bool fresh, const std::string& reason, const bd::Config& cfg) {
    const bd::Candidate empty;
    const bd::Candidate& c = fresh ? r.nearest : empty;
    out << '{';
    if (!cell) {
        std::string risk_reason;
        if (!fresh) risk_reason = reason;
        else if (!r.area) risk_reason = "NO_REGION";
        else if (!c.present) risk_reason = "NO_SUPPORTED_SURFACE";
        else if (r.risk == "DANGER" || r.risk == "CAUTION") risk_reason = "SUPPORTED_NEAR";
        else if (r.risk == "NONE") risk_reason = "OBSERVED_NO_NEAR";
        else risk_reason = "INCOMPLETE_COVERAGE";
        out << "\"level\":\"" << (fresh ? r.risk : "UNKNOWN") << "\",\"reason\":\"" << risk_reason << "\",";
    }
    out << "\"quality\":\"" << (fresh ? r.quality : "UNKNOWN") << "\",";
    candidate_json(out, c);
    out << ",\"observed_ratio\":" << (fresh ? r.accepted_ratio : 0);
    if (cell) out << ",\"outside_attention\":" << (c.present && c.distance_m >= cfg.attention_max_m ? "true" : "false");
    out << '}';
}
void result_json(std::ostream& out, const Frame& f, const bd::SpatialResult& r,
                 const std::string& reason, const bd::Config& cfg) {
    const bool fresh = reason == "OK";
    out << std::setprecision(17) << "{\"schema\":\"basic_depth_v1\",\"coordinate_space\":\""
        << (f.coordinate == 1 ? "native_depth" : "rgb_aligned_saved") << "\",\"source_kind\":\""
        << (f.kind == 1 ? "F32_METRES" : "Z16_RAW") << "\",\"width\":" << f.width
        << ",\"height\":" << f.height << ",\"session\":" << f.session << ",\"seq\":" << (f.gap ? 0 : f.seq)
        << ",\"capture_ns\":" << (f.gap ? f.processing_ns : f.capture_ns) << ",\"processing_ns\":" << f.processing_ns
        << ",\"roi\":[" << r.roi_x << ',' << r.roi_y << ',' << r.roi_x + r.roi_size << ',' << r.roi_y + r.roi_size
        << "],\"frame_state\":{\"fresh\":" << (fresh ? "true" : "false") << ",\"reason\":\"" << reason
        << "\",\"age_ms\":" << (f.gap ? 0 : age_ms(f)) << "},\"matrix\":[";
    for (unsigned i = 0; i < 256; ++i) { if (i) out << ','; out << unsigned(r.matrix[i]); }
    out << "],\"cells\":[";
    for (unsigned i = 0; i < 256; ++i) { if (i) out << ','; region_json(out, r.cells[i], true, fresh, reason, cfg); }
    out << "],\"central\":"; region_json(out, r.center, false, fresh, reason, cfg);
    out << ",\"left\":"; region_json(out, r.left, false, fresh, reason, cfg);
    out << ",\"right\":"; region_json(out, r.right, false, fresh, reason, cfg);
    out << ",\"summary\":{\"observed_pixels\":" << r.accepted_pixels << ",\"invalid_pixels\":" << r.invalid_pixels
        << ",\"outside_model_pixels\":" << r.outside_model_pixels << ",\"outside_attention_pixels\":" << r.outside_attention_pixels
        << ",\"observed_ratio\":" << r.accepted_ratio << ",\"invalid_ratio\":" << r.invalid_ratio
        << ",\"rejected_noise_pixels\":" << r.rejected_noise_pixels << ",\"credible_pixels\":" << r.supported_pixels
        << ",\"component_count_total\":" << r.component_count << ",\"credible_component_count\":" << r.credible_component_count
        << ",\"quality\":\"" << r.quality << "\"}}";
}
}

int main(int argc, char** argv) {
    try {
        bool stream = false, timing = false;
        std::string config_path;
        std::vector<std::string> positional;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--stream") stream = true;
            else if (arg == "--timing") timing = true;
            else if (arg == "--config" && i + 1 < argc) config_path = argv[++i];
            else if (arg.rfind("--", 0) == 0) throw std::runtime_error("unknown/incomplete option: " + arg);
            else positional.push_back(arg);
        }
        if ((!stream && positional.size() != 2) || (stream && !positional.empty()))
            throw std::runtime_error("usage: basic_depth INPUT.bdp OUTPUT.json --config CONFIG.txt [--timing] OR --stream --config CONFIG.txt [--timing]");
        const bd::Config cfg = bd::load_config(config_path);
        bd::validate_config(cfg);
        std::ifstream file;
        std::ofstream output_file;
        std::istream* input = &std::cin;
        std::ostream* output = &std::cout;
        if (!stream) {
            if (std::filesystem::exists(positional[1])) throw std::runtime_error("refusing to overwrite output: " + positional[1]);
            file.open(positional[0], std::ios::binary);
            if (!file) throw std::runtime_error("cannot open input: " + positional[0]);
            input = &file;
        }
#ifdef _WIN32
        if (stream && _setmode(_fileno(stdin), _O_BINARY) == -1) throw std::runtime_error("cannot set binary stdin");
#endif
        Tracker tracker;
        Frame frame;
        unsigned records = 0;
        while (read_frame(*input, frame)) {
            if (!stream && records) throw std::runtime_error("single-file input contains multiple records");
            const auto begin = std::chrono::steady_clock::now();
            auto profile_stage_start = basic_depth_profile::now_ns();
            const std::string reason = tracker.check(frame, cfg);
            basic_depth_profile::frame.freshness_check_ms = basic_depth_profile::elapsed_ms(profile_stage_start);
            profile_stage_start = basic_depth_profile::now_ns();
            const bd::SpatialResult result = reason == "OK" ? bd::analyze(frame.depth, frame.width, frame.height, cfg) : unavailable(frame, cfg);
            basic_depth_profile::frame.analysis_dispatch_ms = basic_depth_profile::elapsed_ms(profile_stage_start);
            profile_stage_start = basic_depth_profile::now_ns();
            std::ostringstream json;
            result_json(json, frame, result, reason, cfg);
            const std::string serialized = json.str();
            basic_depth_profile::frame.json_serialization_ms = basic_depth_profile::elapsed_ms(profile_stage_start);
            profile_stage_start = basic_depth_profile::now_ns();
            if (!stream) {
                if (input->peek() != std::char_traits<char>::eof()) throw std::runtime_error("single-file input has trailing data");
                output_file.open(positional[1], std::ios::binary);
                if (!output_file) throw std::runtime_error("cannot create output: " + positional[1]);
                output = &output_file;
            }
            *output << serialized << '\n';
            output->flush();
            if (!*output) throw std::runtime_error("output write failed");
            basic_depth_profile::frame.output_write_flush_ms = basic_depth_profile::elapsed_ms(profile_stage_start);
            basic_depth_profile::frame.complete_frame_wall_ms = basic_depth_profile::elapsed_ms(basic_depth_profile::frame.read_start_ns);
            basic_depth_profile::frame.complete_processing_without_input_wait_ms = basic_depth_profile::frame.complete_frame_wall_ms - basic_depth_profile::frame.input_read_wait_ms;
            if (timing) {
                const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count();
                std::cerr << std::setprecision(10) << "{\"session\":" << frame.session << ",\"seq\":" << frame.seq
                    << ",\"cpp_processing_json_output_ms\":" << elapsed;
                basic_depth_profile::emit_fields(std::cerr);
                std::cerr << "}\n";
            }
            ++records;
            if (!stream) break;
        }
        if (!stream && records == 0) throw std::runtime_error("input contains no BDP1 record");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "basic_depth: " << e.what() << '\n';
        return 2;
    }
}
