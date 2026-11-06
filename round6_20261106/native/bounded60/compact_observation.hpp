#pragma once
// G6C1: fixed 512-byte observation-only IPC. No capture freshness invention.
// Full GFRegion witnesses are validated here and retained in frames.jsonl;
// the compact IPC is not a replacement for those forensic records.
#include "gemini_geometry.h"
#include <array>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <cstdio>
#include <chrono>

struct R6CompactMeta {
    uint32_t session, reserved;
    uint64_t sequence, sdk_index, session_started_ns, wait_begin_ns, wait_end_ns;
    uint64_t processing_begin_ns, processing_end_ns, encoding_begin_ns;
    uint64_t sdk_device_us, sdk_system_us;
    double scale_m, scale_sdk_mm;
    uint32_t raw_valid_pixels, raw_zero_pixels, raw_out_of_range_pixels;
    uint8_t raw_sha256[32], identity_sha256[32];
};

namespace r6compact {
constexpr unsigned BYTES=512,HEADER=224,MATRIX=224,UNKNOWN=480;
inline uint32_t crc32(const uint8_t* data,size_t n) {
    uint32_t crc=0xffffffffu;
    for(size_t i=0;i<n;++i) {crc^=data[i];for(unsigned b=0;b<8;++b)crc=(crc>>1)^((0u-(crc&1u))&0xedb88320u);}
    return ~crc;
}
inline void u16(uint8_t* p,unsigned v){p[0]=uint8_t(v);p[1]=uint8_t(v>>8);}
inline void u32(uint8_t* p,uint32_t v){for(unsigned i=0;i<4;++i)p[i]=uint8_t(v>>(8*i));}
inline void u64(uint8_t* p,uint64_t v){for(unsigned i=0;i<8;++i)p[i]=uint8_t(v>>(8*i));}
inline void f64(uint8_t* p,double v){uint64_t bits;std::memcpy(&bits,&v,8);u64(p,bits);}
inline int reject(char* error,size_t n,const char* why,int region=-1) {
    if(error&&n)std::snprintf(error,n,"%s:region=%d",why,region);
    return 1;
}
inline int quantize(double z) {
    if(z>=2.5)return 0;
    if(z<=.3+1e-6)return 4;
    return std::max(1,std::min(4,int(std::floor(4.-3.*(z-.3)/2.2+1e-6))));
}
inline bool config_exact(const GFConfig& c) {
    return c.width==640&&c.height==400&&c.tile_size==32&&c.halo_pixels==3&&c.component_min_pixels==4&&
      c.depth_band_m==.08&&c.adjacency_max_step_m==.08&&c.measurement_min_m==.1&&c.measurement_max_m==6.&&
      c.attention_max_m==2.5&&c.danger_m==.8&&c.caution_m==1.5&&c.complete_ratio==.9&&c.quantize_min_m==.3;
}
inline int validate_region(const GFRegion& r,unsigned i,char* error,size_t n) {
    const unsigned row=i/16,col=i%16;
    const int b[4]={i<256?120+int(col)*25:i==256?0:520,
                    i<256?int(row)*25:0,
                    i<256?145+int(col)*25:i==256?120:640,
                    i<256?25+int(row)*25:400};
    for(unsigned k=0;k<4;++k)if(r.bounds[k]!=b[k])return reject(error,n,"REGION_BOUNDS",i);
    const int area=(b[2]-b[0])*(b[3]-b[1]);
    if(r.total_pixels!=area||r.valid_pixels<0||r.valid_pixels>area||r.coherent_pixels<0||r.coherent_pixels>r.valid_pixels)
        return reject(error,n,"REGION_PIXEL_COUNTS",i);
    // These values are written unconditionally by the retained full JSON
    // formatter, including empty/no-candidate regions. The old JSON ingress
    // rejected non-finite numbers before its quality validator; binary IPC
    // must retain that gate rather than publish an unparseable evidence row.
    if(!std::isfinite(r.depth_band_low_m)||!std::isfinite(r.depth_band_high_m)||
       !std::isfinite(r.actual_depth_min_m)||!std::isfinite(r.actual_depth_max_m)||
       !std::isfinite(r.adjacency_max_step_m))
        return reject(error,n,"NONFINITE_FULL_REGION_EVIDENCE",i);
    if(!std::isfinite(r.coverage)||!std::isfinite(r.coherent_coverage)||
       r.coverage<0.0||r.coverage>1.0||r.coherent_coverage<0.0||r.coherent_coverage>r.coverage||
       std::fabs(r.coverage-double(r.valid_pixels)/area)>=1e-12||
       std::fabs(r.coherent_coverage-double(r.coherent_pixels)/area)>=1e-12)
        return reject(error,n,"REGION_COVERAGE_COUNTS",i);
    if(r.unsupported_near_pixels<0||r.unsupported_near_pixels>r.valid_pixels-r.coherent_pixels)
        return reject(error,n,"UNSUPPORTED_NEAR_COUNTS",i);
    if(r.component_support<0||r.region_support<0||r.region_support>r.component_support)
        return reject(error,n,"ACTUAL_COMPONENT_AND_REGION_SUPPORT_REQUIRED",i);
    if(r.has_candidate!=0&&r.has_candidate!=1)return reject(error,n,"NATIVE_BOOLEAN_FIELDS",i);
    if(r.quality<0||r.quality>2)return reject(error,n,"REGION_QUALITY_UNKNOWN",i);
    if(r.has_candidate==0) {
        // The JSON formatter emits distance/uv/witness=null for this case;
        // unused GF scratch distance/uv values are not interpreted as evidence.
        if(r.component_support||r.region_support||r.quality!=0||r.level!=0)
            return reject(error,n,"MISSING_CANDIDATE_SEMANTICS",i);
        return 0;
    }
    if(!std::isfinite(r.distance_m)||r.distance_m<.1||r.distance_m>6.||r.component_support<4||
        r.region_support<=0||r.region_support>r.coherent_pixels)
        return reject(error,n,"CANDIDATE_ACTUAL_SUPPORT",i);
    if(r.uv[0]<b[0]||r.uv[0]>=b[2]||r.uv[1]<b[1]||r.uv[1]>=b[3])
        return reject(error,n,"CANDIDATE_UV",i);
    if(r.method!=1&&r.method!=2)return reject(error,n,"UNRECOGNIZED_NATIVE_WITNESS_METHOD",i);
    const int* w=r.witness_bounds;
    if(w[0]<0||w[1]<0||w[0]>r.uv[0]||w[1]>r.uv[1]||w[2]<=r.uv[0]||w[3]<=r.uv[1]||w[2]>640||w[3]>400||
        int64_t(r.component_support)>int64_t(w[2]-w[0])*(w[3]-w[1]))
        return reject(error,n,"WITNESS_SUPPORT_BOUNDS",i);
    if(!std::isfinite(r.actual_depth_min_m)||!std::isfinite(r.actual_depth_max_m)||
        r.actual_depth_min_m<.1||r.actual_depth_min_m>r.distance_m||r.actual_depth_max_m<r.distance_m||r.actual_depth_max_m>6.)
        return reject(error,n,"WITNESS_DEPTH_RANGE",i);
    const bool complete=r.coverage>=.9&&r.coherent_coverage>=.9;
    const int expected=r.unsupported_near_pixels?0:complete?1:2;
    if(r.quality!=expected)return reject(error,n,"QUALITY_PROMOTION_OR_INCONSISTENCY",i);
    if(r.level!=quantize(r.distance_m))return reject(error,n,"REGION_LEVEL_QUANTIZATION",i);
    return 0;
}
inline void side(uint8_t* out,const GFRegion& r) {
    const bool complete=r.coverage>=.9&&r.coherent_coverage>=.9;
    if(!r.has_candidate) {out[0]=0;out[1]=complete?6:2;u16(out+2,0xffff);return;}
    const bool unknown=r.quality!=1;
    out[0]=r.distance_m>=2.5?(unknown?5:1):r.distance_m<.8?4:r.distance_m<1.5?3:2;
    out[1]=unknown?3:5;
    u16(out+2,std::max(1,int(std::floor(r.distance_m*1000.+.5))));
}
inline int encode(const GFConfig& c,const GFRegion* regions,const R6CompactMeta& m,uint8_t* out,char* error,size_t n) {
    if(!regions||!out)return reject(error,n,"NULL_INPUT");
    std::fill(out,out+BYTES,uint8_t(0));
    const auto begin=std::chrono::steady_clock::now();
    if(!config_exact(c))return reject(error,n,"FROZEN_GEMINI_CONFIG_MISMATCH");
    if(!m.session||!m.sequence||m.sequence>UINT32_MAX||m.reserved||
        !(m.session_started_ns>0&&m.session_started_ns<=m.wait_begin_ns&&m.wait_begin_ns<=m.wait_end_ns&&
          m.wait_end_ns<=m.processing_begin_ns&&m.processing_begin_ns<=m.processing_end_ns&&m.processing_end_ns<=m.encoding_begin_ns))
        return reject(error,n,"LIVE_HOST_CLOCK_ORDER_OR_SOURCE_ID");
    if(!std::isfinite(m.scale_m)||!std::isfinite(m.scale_sdk_mm)||m.scale_m<=0||m.scale_sdk_mm<=0||
        std::fabs(m.scale_m-m.scale_sdk_mm*.001)>1e-15)
        return reject(error,n,"SDK_DEPTH_SCALE_EVIDENCE_REQUIRED");
    if(uint64_t(m.raw_valid_pixels)+m.raw_zero_pixels+m.raw_out_of_range_pixels!=640u*400u)
        return reject(error,n,"RAW_PIXEL_COUNT_PARTITION");
    if(std::all_of(m.raw_sha256,m.raw_sha256+32,[](uint8_t x){return x==0;})||
       std::all_of(m.identity_sha256,m.identity_sha256+32,[](uint8_t x){return x==0;}))
        return reject(error,n,"RAW_AND_DEVICE_DIGEST_REQUIRED");
    bool complete=true,known=false;unsigned unknown_count=0;
    for(unsigned i=0;i<258;++i) {
        if(validate_region(regions[i],i,error,n))return 1;
        const auto& r=regions[i];complete&=r.quality==1;known|=r.has_candidate!=0;
        if(i<256) {out[MATRIX+i]=uint8_t(r.level);if(r.quality!=1){out[UNKNOWN+i/8]|=uint8_t(1u<<(i%8));++unknown_count;}}
    }
    std::memcpy(out,"G6C1",4);u16(out+4,1);u16(out+6,HEADER);u32(out+8,BYTES);u32(out+16,15);
    u32(out+20,m.session);u64(out+24,m.sequence);u64(out+32,m.sdk_index);u64(out+40,m.session_started_ns);
    u64(out+48,m.wait_begin_ns);u64(out+56,m.wait_end_ns);u64(out+64,m.processing_begin_ns);u64(out+72,m.processing_end_ns);
    u64(out+80,m.encoding_begin_ns);u64(out+88,m.sdk_device_us);u64(out+96,m.sdk_system_us);
    std::memcpy(out+104,m.raw_sha256,32);std::memcpy(out+136,m.identity_sha256,32);
    u16(out+168,640);u16(out+170,400);u32(out+172,1280);f64(out+176,m.scale_m);
    u32(out+184,m.raw_valid_pixels);u32(out+188,m.raw_zero_pixels);u32(out+192,m.raw_out_of_range_pixels);
    u16(out+196,unknown_count);out[198]=complete?0:known?1:3;
    side(out+200,regions[256]);side(out+204,regions[257]);
    const auto elapsed=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-begin).count();
    u64(out+208,uint64_t(elapsed));u32(out+12,crc32(out,BYTES));
    if(error&&n)error[0]=0;
    return 0;
}
}

#if defined(_WIN32)
#define R6_COMPACT_API extern "C" __declspec(dllexport)
#else
#define R6_COMPACT_API extern "C" __attribute__((visibility("default")))
#endif
R6_COMPACT_API int r6_compact_encode(const GFConfig*,const GFRegion*,const R6CompactMeta*,uint8_t*,char*,uint32_t);
