#include "basic_depth.hpp"
#include "phase_profile.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <numeric>
#include <set>
#include <stdexcept>
#include <memory>

namespace basic_depth {
namespace {
struct Dsu {
    std::vector<int> parent;
    std::vector<unsigned> size;
    explicit Dsu(std::size_t n) : parent(n, -1), size(n, 0) {}
    int find(int a) {
        int root = a;
        while (parent[root] != root) root = parent[root];
        while (a != root) {
            int next = parent[a]; parent[a] = root; a = next;
        }
        return root;
    }
    int join_roots(int a, int b) {
        if (a == b) return a;
        if (size[a] < size[b] || (size[a] == size[b] && a > b)) std::swap(a, b);
        parent[b] = a; size[a] += size[b];
        return a;
    }
};
std::string trim(const std::string& s) {
    const auto begin = s.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) return {};
    return s.substr(begin, s.find_last_not_of(" \t\r\n") - begin + 1);
}
bool accepted(float z, const Config& cfg) {
    return std::isfinite(z) && static_cast<double>(z) >= cfg.measurement_min_m &&
           static_cast<double>(z) <= cfg.measurement_max_m;
}
bool joined(float a, float b, const Config& cfg) {
    const double az = a, bz = b;
    const double delta = std::abs(az - bz);
    // Both inputs are already accepted finite positive float32 values.
    // delta<=max(abs,relative) iff delta<=abs OR delta<=relative.
    if (delta <= cfg.connect_abs_m) return true;
    return delta <= cfg.connect_rel * std::min(az, bz);
}
void update_candidate(Region& r, const std::vector<unsigned>& indices,
                      unsigned component_support, const std::vector<float>& z,
                      unsigned width, const Config& cfg, std::vector<float>& values) {
    values.clear();
    if (values.capacity() < indices.size()) values.reserve(indices.size());
    for (unsigned idx : indices) values.push_back(z[idx]);
    const std::size_t k = static_cast<std::size_t>(std::floor((values.size() - 1) * cfg.percentile / 100.0));
    std::nth_element(values.begin(), values.begin() + k, values.end());
    const double distance = values[k];
    unsigned pixel = std::numeric_limits<unsigned>::max();
    for (unsigned idx : indices) if (static_cast<double>(z[idx]) == distance) pixel = std::min(pixel, idx);
    if (!r.nearest.present || distance < r.nearest.distance_m ||
        (distance == r.nearest.distance_m && pixel < r.nearest.v * width + r.nearest.u)) {
        r.nearest.present = true; r.nearest.distance_m = distance;
        r.nearest.u = pixel % width; r.nearest.v = pixel / width;
        r.nearest.support = component_support;
        r.nearest.region_pixels = static_cast<unsigned>(indices.size());
    }
}
void finish_region(Region& r) {
    r.accepted_ratio = r.area ? static_cast<double>(r.accepted_pixels) / r.area : 0.0;
}
void classify_region(Region& r, const Config& cfg) {
    if (r.nearest.present) r.quality = r.accepted_ratio >= cfg.complete_ratio ? "VALID" : "SPARSE";
    if (r.nearest.present && r.nearest.distance_m < cfg.danger_m) r.risk = "DANGER";
    else if (r.nearest.present && r.nearest.distance_m < cfg.caution_m) r.risk = "CAUTION";
    else if (r.nearest.present && r.quality == "VALID") r.risk = "NONE";
}
}

Config load_config(const std::string& path) {
    Config cfg;
    if (path.empty()) return cfg;
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open config: " + path);
    std::string line;
    std::set<std::string> seen;
    unsigned line_no = 0;
    while (std::getline(in, line)) {
        ++line_no;
        for (unsigned char ch : line) if (ch > 127) throw std::runtime_error("configuration must be ASCII");
        const auto hash = line.find('#');
        if (hash != std::string::npos) line.resize(hash);
        line = trim(line);
        if (line.empty()) continue;
        const auto equal = line.find('=');
        if (equal == std::string::npos) throw std::runtime_error("config needs key=value at line " + std::to_string(line_no));
        const std::string key = trim(line.substr(0, equal));
        if (!seen.insert(key).second) throw std::runtime_error("duplicate config key: " + key);
        const std::string value = trim(line.substr(equal + 1));
        std::size_t used = 0;
        const double v = std::stod(value, &used);
        if (used != value.size() || !std::isfinite(v)) throw std::runtime_error("invalid config value: " + key);
        if (key == "measurement_min_m") cfg.measurement_min_m = v;
        else if (key == "measurement_max_m") cfg.measurement_max_m = v;
        else if (key == "connect_abs_m") cfg.connect_abs_m = v;
        else if (key == "connect_rel") cfg.connect_rel = v;
        else if (key == "surface_min_pixels") {
            if (v < 2 || v != std::floor(v) || v > 10000000) throw std::runtime_error("surface_min_pixels must be an integer >=2");
            cfg.surface_min_pixels = static_cast<unsigned>(v);
        }
        else if (key == "percentile") cfg.percentile = v;
        else if (key == "complete_ratio") cfg.complete_ratio = v;
        else if (key == "quantize_min_m") cfg.quantize_min_m = v;
        else if (key == "attention_max_m") cfg.attention_max_m = v;
        else if (key == "danger_m") cfg.danger_m = v;
        else if (key == "caution_m") cfg.caution_m = v;
        else if (key == "stale_ms") cfg.stale_ms = v;
        else if (key == "future_tolerance_ms") cfg.future_tolerance_ms = v;
        else throw std::runtime_error("unknown config key: " + key);
    }
    validate_config(cfg);
    return cfg;
}

void validate_config(const Config& c) {
    const double fields[] = {c.measurement_min_m, c.measurement_max_m, c.connect_abs_m, c.connect_rel,
        c.percentile, c.complete_ratio, c.quantize_min_m, c.attention_max_m, c.danger_m,
        c.caution_m, c.stale_ms, c.future_tolerance_ms};
    for (double v : fields) if (!std::isfinite(v)) throw std::runtime_error("configuration must be finite");
    if (c.measurement_min_m <= 0 || c.measurement_max_m <= c.measurement_min_m ||
        c.connect_abs_m <= 0 || c.connect_rel < 0 || c.surface_min_pixels < 2 ||
        c.percentile < 0 || c.percentile > 100 || c.percentile != std::floor(c.percentile) ||
        c.complete_ratio <= 0 || c.complete_ratio > 1 ||
        c.quantize_min_m <= 0 || c.attention_max_m <= c.quantize_min_m || c.attention_max_m > c.measurement_max_m ||
        c.danger_m <= 0 || c.caution_m <= c.danger_m || c.caution_m > c.attention_max_m || c.stale_ms <= 0 ||
        c.future_tolerance_ms < 0) throw std::runtime_error("configuration bounds invalid");
}

unsigned char quantize(double distance_m, const Config& cfg) {
    if (!std::isfinite(distance_m) || distance_m <= 0) return 0;
    if (distance_m <= cfg.quantize_min_m + 1e-6) return 4;
    const double ratio = (distance_m - cfg.quantize_min_m) / (cfg.attention_max_m - cfg.quantize_min_m);
    if (ratio >= 1) return 0;
    return static_cast<unsigned char>(std::clamp(static_cast<int>(4 - ratio * 3 + 1e-6), 1, 4));
}

SpatialResult analyze(const std::vector<float>& z, unsigned w, unsigned h, const Config& cfg) {
    basic_depth_profile::AlgorithmScope algorithm_scope;
    auto profile_phase_start = basic_depth_profile::now_ns();
    validate_config(cfg);
    if (w < 16 || h < 16 || w > 4096 || h > 4096 || z.size() != static_cast<std::size_t>(w) * h)
        throw std::runtime_error("input dimensions must be 16..4096 and match payload");
    const unsigned n = w * h;
    basic_depth_profile::frame.algorithm_validate_setup_ms = basic_depth_profile::elapsed_ms(profile_phase_start);
    profile_phase_start = basic_depth_profile::now_ns();
    Dsu dsu(n);
    unsigned naccepted = 0;
    for (unsigned i = 0; i < n; ++i) if (accepted(z[i], cfg)) {
        dsu.parent[i] = static_cast<int>(i); dsu.size[i] = 1; ++naccepted;
    }
    for (unsigned y = 0; y < h; ++y) for (unsigned x = 0; x < w; ++x) {
        const unsigned i = y * w + x;
        if (dsu.parent[i] < 0) continue;
        int root = dsu.find(static_cast<int>(i));
        auto connect = [&](unsigned other) {
            if (dsu.parent[other] < 0) return;
            const int neighbor_root = dsu.find(static_cast<int>(other));
            if (root != neighbor_root && joined(z[i], z[other], cfg)) root = dsu.join_roots(root, neighbor_root);
        };
        // Same four preceding neighbors, in exactly the original order.
        if (x) connect(i - 1);
        if (y) {
            const unsigned above = i - w;
            if (x) connect(above - 1);
            connect(above);
            if (x + 1 < w) connect(above + 1);
        }
    }
    basic_depth_profile::frame.connectivity_build_ms = basic_depth_profile::elapsed_ms(profile_phase_start);
    profile_phase_start = basic_depth_profile::now_ns();
    SpatialResult result;
    result.width = w; result.height = h; result.roi_size = std::min(w, h);
    result.roi_x = w / 2 - result.roi_size / 2; result.roi_y = h / 2 - result.roi_size / 2;
    result.accepted_pixels = naccepted; result.accepted_ratio = static_cast<double>(naccepted) / n;
    std::array<unsigned, 256> cell_accepted{};
    unsigned center_accepted = 0, left_accepted = 0, right_accepted = 0;
    std::vector<int> heads(n, -1);
    std::unique_ptr<int[]> next(new int[n]);
    std::unique_ptr<std::uint16_t[]> region_for(new std::uint16_t[n]);
    std::vector<unsigned> roots;
    roots.reserve(128);
    for (unsigned i = 0; i < n; ++i) {
        if (dsu.parent[i] < 0) continue;
        const int root = dsu.find(static_cast<int>(i));
        dsu.parent[i] = root;
        const unsigned x = i % w, y = i / w;
        if (root == static_cast<int>(i)) ++result.component_count;
        const bool credible = dsu.size[root] >= cfg.surface_min_pixels;
        if (credible) ++result.supported_pixels;
        unsigned region = 0xffffu;
        if (x < result.roi_x) { ++left_accepted; region = 256; }
        else if (x >= result.roi_x + result.roi_size) { ++right_accepted; region = 257; }
        else if (y >= result.roi_y && y < result.roi_y + result.roi_size) {
            ++center_accepted;
            const unsigned row = ((y - result.roi_y + 1) * 16 - 1) / result.roi_size;
            const unsigned col = ((x - result.roi_x + 1) * 16 - 1) / result.roi_size;
            region = row * 16 + col;
            ++cell_accepted[region];
        }
        if (credible && region != 0xffffu) {
            if (heads[root] < 0) roots.push_back(static_cast<unsigned>(root));
            region_for[i] = static_cast<std::uint16_t>(region);
            next[i] = heads[root]; heads[root] = static_cast<int>(i);
        }
    }
    basic_depth_profile::frame.component_grouping_ms = basic_depth_profile::elapsed_ms(profile_phase_start);
    profile_phase_start = basic_depth_profile::now_ns();
    result.center.area = result.roi_size * result.roi_size;
    result.center.accepted_pixels = center_accepted;
    result.center.accepted_ratio = static_cast<double>(center_accepted) / result.center.area;
    result.left.area = result.roi_x * h; result.left.accepted_pixels = left_accepted; finish_region(result.left);
    result.right.area = (w - result.roi_x - result.roi_size) * h; result.right.accepted_pixels = right_accepted; finish_region(result.right);
    for (unsigned row = 0; row < 16; ++row) for (unsigned col = 0; col < 16; ++col) {
        const unsigned cell = row * 16 + col;
        result.cells[cell].area = ((row + 1) * result.roi_size / 16 - row * result.roi_size / 16) *
                                  ((col + 1) * result.roi_size / 16 - col * result.roi_size / 16);
        result.cells[cell].accepted_pixels = cell_accepted[cell]; finish_region(result.cells[cell]);
    }
    std::array<std::vector<unsigned>, 258> buckets;
    std::vector<unsigned> active_regions; active_regions.reserve(258);
    std::vector<float> values;
    for (unsigned root : roots) {
        active_regions.clear();
        for (int i = heads[root]; i >= 0; i = next[static_cast<unsigned>(i)]) {
            const unsigned region = region_for[static_cast<unsigned>(i)];
            if (buckets[region].empty()) active_regions.push_back(region);
            buckets[region].push_back(static_cast<unsigned>(i));
        }
        for (unsigned region : active_regions) {
            Region& target = region < 256 ? result.cells[region] : (region == 256 ? result.left : result.right);
            update_candidate(target, buckets[region], dsu.size[root], z, w, cfg, values);
            buckets[region].clear();
        }
    }
    classify_region(result.left, cfg); classify_region(result.right, cfg);
    for (unsigned cell = 0; cell < 256; ++cell) {
        classify_region(result.cells[cell], cfg);
        if (result.cells[cell].nearest.present) result.matrix[cell] = quantize(result.cells[cell].nearest.distance_m, cfg);
        const Candidate& a = result.cells[cell].nearest;
        const Candidate& b = result.center.nearest;
        if (a.present && (!b.present || a.distance_m < b.distance_m ||
            (a.distance_m == b.distance_m && a.v * w + a.u < b.v * w + b.u))) result.center.nearest = a;
    }
    basic_depth_profile::frame.region_candidate_and_local_quality_ms = basic_depth_profile::elapsed_ms(profile_phase_start);
    profile_phase_start = basic_depth_profile::now_ns();
    if (result.center.nearest.present) {
        result.center.quality = result.center.accepted_ratio >= cfg.complete_ratio ? "VALID" : "SPARSE";
        if (result.center.nearest.distance_m < cfg.danger_m) result.center.risk = "DANGER";
        else if (result.center.nearest.distance_m < cfg.caution_m) result.center.risk = "CAUTION";
        else if (result.center.quality == "VALID") result.center.risk = "NONE";
    }
    unsigned valid_cells = 0, candidates = 0;
    for (const Region& r : result.cells) {
        valid_cells += r.quality == "VALID"; candidates += r.nearest.present;
    }
    result.quality = valid_cells == 256 ? "VALID" : (candidates ? "PARTIAL" : "UNKNOWN");
    for (unsigned i = 0; i < n; ++i) {
        if (!std::isfinite(z[i]) || z[i] <= 0) ++result.invalid_pixels;
        else if (!accepted(z[i], cfg)) ++result.outside_model_pixels;
        else if (static_cast<double>(z[i]) >= cfg.attention_max_m) ++result.outside_attention_pixels;
        if (dsu.parent[i] == static_cast<int>(i) && dsu.size[i] >= cfg.surface_min_pixels) ++result.credible_component_count;
    }
    result.invalid_ratio = static_cast<double>(result.invalid_pixels) / n;
    result.rejected_noise_pixels = naccepted - result.supported_pixels;
    basic_depth_profile::frame.final_quality_statistics_ms = basic_depth_profile::elapsed_ms(profile_phase_start);
    return result;
}
}  // namespace basic_depth
