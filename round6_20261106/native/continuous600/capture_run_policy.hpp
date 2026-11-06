#pragma once
// SDK-free run limits and bounded retention policy. No device or IPC operations.
#include <algorithm>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>
namespace r6capture {
constexpr uint64_t evidence_record_limit=1800;
constexpr uint64_t sequence_limit=UINT32_MAX;
struct RunPolicy {
    bool continuous=false;
    uint64_t goal=0,session=0,samples=0,duration_ms=0;
    static RunPolicy parse(int argc,char** argv) {
        if(argc!=8&&argc!=9)throw std::runtime_error("INVALID_ARGUMENT_COUNT");
        RunPolicy p;
        if(argc==9) {
            if(std::string(argv[8])!="--continuous")throw std::runtime_error("UNKNOWN_CONTINUOUS_OPTION");
            p.continuous=true;
        }
        // Preserve the previous bounded CLI's numeric conversion and ranges.
        p.goal=std::stoull(argv[3]);p.session=std::stoull(argv[4]);
        p.samples=std::stoull(argv[5]);p.duration_ms=std::stoull(argv[7]);
        if(p.session<1||p.session>UINT32_MAX||p.samples>64)
            throw std::runtime_error("INVALID_BOUNDED_ARGUMENTS");
        if(p.continuous) {
            if(p.goal||p.duration_ms)throw std::runtime_error("CONTINUOUS_REQUIRES_ZERO_FRAME_AND_DURATION_LIMITS");
        } else if(p.goal<1||p.goal>1800||p.duration_ms<1||p.duration_ms>120000)
            throw std::runtime_error("INVALID_BOUNDED_ARGUMENTS");
        return p;
    }
    bool capture_time_available(uint64_t now,uint64_t deadline) const {
        return continuous||now<deadline;
    }
    bool capture_goal_reached(uint64_t captured) const {
        return !continuous&&captured>=goal;
    }
    bool persist_record(uint64_t sequence) const {
        return !continuous||sequence<=evidence_record_limit;
    }
    bool sequence_available(uint64_t processed) const {return processed<sequence_limit;}
    bool requested_stop_is_normal(bool requested,uint64_t processed) const {
        return continuous&&requested&&processed>0;
    }
    bool log_sequence_rejection(uint64_t total) const {
        return !continuous||total<=16;
    }
};
inline void retain_timing(std::vector<uint64_t>& samples,uint64_t value,
                          uint64_t seen,bool continuous) {
    if(!seen)throw std::runtime_error("TIMING_SAMPLE_SEQUENCE_ZERO");
    if(!continuous||samples.size()<evidence_record_limit)samples.push_back(value);
    else samples[(seen-1)%evidence_record_limit]=value;
}
inline std::vector<uint64_t> ordered_timings(const std::vector<uint64_t>& samples,
                                           uint64_t seen,bool continuous) {
    auto values=samples;
    if(continuous&&values.size()==evidence_record_limit&&seen>values.size())
        std::rotate(values.begin(),values.begin()+seen%evidence_record_limit,values.end());
    return values;
}
} // namespace r6capture
