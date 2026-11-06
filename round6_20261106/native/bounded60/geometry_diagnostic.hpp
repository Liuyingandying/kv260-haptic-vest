#pragma once
// Observation-only formatter; this is NOT the frozen V2/producer schema.
// Geometry is linked unchanged from the build_03 source. No KPF1/MMIO here.
#include "gemini_geometry.h"
#include <array>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

static_assert(sizeof(GFConfig)==96,"GFConfig ABI v1 size mismatch");
static_assert(sizeof(GFRegion)==176,"GFRegion ABI v1 size mismatch");
static_assert(sizeof(GFCounts)==20,"GFCounts ABI v1 size mismatch");
static_assert(sizeof(GFStageTimes)==48,"GFStageTimes ABI v1 size mismatch");

inline GFConfig frozen_gemini_config() {
    return {640,400,32,3,4,0.08,0.08,0.1,6.0,2.5,0.8,1.5,0.90,0.3};
}
inline void decode_y16(const std::vector<uint16_t>& raw, double scale_m,
                       const GFConfig& c, std::vector<double>& meters,
                       std::vector<uint8_t>& valid) {
    meters.resize(raw.size()); valid.resize(raw.size());
    for(size_t i=0;i<raw.size();++i) {
        const double d=raw[i]*scale_m;
        meters[i]=d;
        valid[i]=std::isfinite(d)&&d>=c.measurement_min_m&&d<=c.measurement_max_m;
    }
}
inline const char* quality_name(int q) { return q==1?"VALID":q==2?"SPARSE":"UNKNOWN"; }
inline const char* candidate_risk(const GFRegion& r) {
    if(!r.has_candidate)return "UNKNOWN";
    return r.distance_m<0.8?"DANGER":r.distance_m<1.5?"CAUTION":r.distance_m<2.5?"ATTENTION":"OUTSIDE_ATTENTION";
}
inline std::string geometry_quality(const std::array<GFRegion,258>& rs) {
    bool known=false,complete=true;
    for(const auto& r:rs) { if(r.total_pixels<=0)continue; known|=r.has_candidate!=0; complete&=r.quality==1; }
    return complete?"COMPLETE":known?"PARTIAL":"UNUSABLE";
}
inline void region_json(std::ostream& o,const GFRegion& r) {
    o<<"{\"bounds\":["<<r.bounds[0]<<','<<r.bounds[1]<<','<<r.bounds[2]<<','<<r.bounds[3]
      <<"],\"total_pixels\":"<<r.total_pixels<<",\"valid_pixels\":"<<r.valid_pixels
      <<",\"coherent_pixels\":"<<r.coherent_pixels<<",\"unsupported_near_pixels\":"<<r.unsupported_near_pixels
      <<",\"has_candidate\":"<<(r.has_candidate?"true":"false")<<",\"quality\":\""<<quality_name(r.quality)
      <<"\",\"unknown\":"<<(r.quality!=1?"true":"false")<<",\"uv\":";
    if(r.has_candidate)o<<'['<<r.uv[0]<<','<<r.uv[1]<<']';else o<<"null";
    o<<",\"distance_m\":";if(r.has_candidate)o<<r.distance_m;else o<<"null";
    o<<",\"component_support\":"<<r.component_support<<",\"region_support\":"<<r.region_support
      <<",\"level\":"<<r.level<<",\"critical_unknown\":"<<(r.unsupported_near_pixels?"true":"false")
      <<",\"candidate_risk\":\""<<candidate_risk(r)<<"\",\"risk\":\""<<(r.unsupported_near_pixels?"UNKNOWN":candidate_risk(r))
      <<"\",\"coverage\":"<<r.coverage<<",\"coherent_coverage\":"<<r.coherent_coverage
      <<",\"witness_id\":"<<r.witness_id<<",\"phase_index\":"<<r.phase_index<<",\"risk_zone\":"<<r.risk_zone
      <<",\"method\":"<<r.method<<",\"tile_core_bounds\":["<<r.tile_core_bounds[0]<<','<<r.tile_core_bounds[1]
      <<','<<r.tile_core_bounds[2]<<','<<r.tile_core_bounds[3]<<"],\"witness_bounds\":["<<r.witness_bounds[0]
      <<','<<r.witness_bounds[1]<<','<<r.witness_bounds[2]<<','<<r.witness_bounds[3]
      <<"],\"depth_band_low_m\":"<<r.depth_band_low_m<<",\"depth_band_high_m\":"<<r.depth_band_high_m
      <<",\"actual_depth_min_m\":"<<r.actual_depth_min_m<<",\"actual_depth_max_m\":"<<r.actual_depth_max_m
      <<",\"adjacency_max_step_m\":"<<r.adjacency_max_step_m<<'}';
}
inline void geometry_json(std::ostream& o,const std::array<GFRegion,258>& rs,
                          const GFCounts& n,const GFStageTimes& t) {
    o<<std::setprecision(17)<<"\"geometry_quality\":\""<<geometry_quality(rs)<<"\",\"matrix_flat\":[";
    int unknown=0;std::array<unsigned,32> bitmap{};
    for(int i=0;i<256;++i){if(i)o<<',';o<<rs[i].level;if(rs[i].quality!=1){++unknown;bitmap[i/8]|=1u<<(i%8);}}
    o<<"],\"unknown_mask_bytes\":[";
    for(int i=0;i<32;++i){if(i)o<<',';o<<bitmap[i];}
    o<<"],\"unknown_cells\":"<<unknown<<",\"regions\":[";
    for(int i=0;i<258;++i){if(i)o<<',';region_json(o,rs[i]);}
    o<<"],\"counts\":{\"accepted_pixels\":"<<n.accepted_pixels<<",\"coherent_pixels\":"<<n.coherent_pixels
      <<",\"adjacency_pixels\":"<<n.adjacency_pixels<<",\"unsupported_near_pixels\":"<<n.unsupported_near_pixels
      <<",\"witness_count\":"<<n.witness_count<<"},\"native_stage_ns\":{\"build_witness_ns\":"<<t.build_witness_ns
      <<",\"adjacency_ns\":"<<t.adjacency_ns<<",\"region_ns\":"<<t.region_ns
      <<",\"candidate_percentile_ns\":"<<t.candidate_percentile_ns<<",\"mapping_ns\":"<<t.mapping_ns
      <<",\"total_ns\":"<<t.total_ns<<'}';
}
