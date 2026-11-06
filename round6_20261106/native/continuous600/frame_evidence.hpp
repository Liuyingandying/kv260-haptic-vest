#pragma once
// Full immutable evidence snapshots. No SDK/IPC/MMIO; serialization runs on one writer.
#include "geometry_diagnostic.hpp"
#include <fstream>
#include <fcntl.h>
#include <unistd.h>
#include <stdexcept>
inline std::string evidence_quoted(const std::string& s) {
    std::ostringstream o;o<<'"';for(unsigned char c:s){if(c=='"'||c=='\\')o<<'\\'<<char(c);else if(c=='\n')o<<"\\n";else if(c=='\r')o<<"\\r";else if(c=='\t')o<<"\\t";else if(c<32)o<<'?';else o<<char(c);}return o.str()+'"';
}
struct EvidenceFrame {
    std::vector<uint16_t> raw;uint64_t index=0,device_us=0,system_us=0,wait_begin=0,wait_end=0,copy_end=0,published=0;
    double scale_sdk=0,scale_m=0;
};

struct FrameEvidenceRecord {
    EvidenceFrame frame;
    std::string device_identity_json,raw_sha256;
    uint64_t sequence=0,session=0,capture_session_started=0,raw_hash_ns=0;
    uint64_t processing_begin=0,converted=0,processing_end=0;
    int64_t raw_valid_pixels=0,raw_zero_pixels=0;
    std::array<GFRegion,258> regions{}; GFCounts counts{}; GFStageTimes timing{};
    std::array<uint8_t,512> compact_packet{};
};
inline std::string render_frame_record(const FrameEvidenceRecord& e) {
    const auto* f=&e.frame; const auto& regions=e.regions; const auto& counts=e.counts; const auto& timing=e.timing;
    const auto processed=e.sequence,session=e.session,capture_session_started=e.capture_session_started;
    const auto processing_begin=e.processing_begin,converted=e.converted,processing_end=e.processing_end;
    const auto raw_hash_begin=uint64_t(0),raw_hash_end=e.raw_hash_ns;
    const auto raw_valid_pixels=e.raw_valid_pixels,raw_zero_pixels=e.raw_zero_pixels;
    const auto& raw_sha256=e.raw_sha256;
            std::ostringstream record;record<<std::setprecision(17);
            record<<"{\"device_identity\":"<<e.device_identity_json<<",\"capture_session_started_monotonic_ns\":"<<capture_session_started<<",\"raw_sha256\":"<<evidence_quoted(raw_sha256)<<",\"raw_hash_ns\":"<<(raw_hash_end-raw_hash_begin)<<",\"schema\":\"GEMINI_NATIVE_DIAGNOSTIC_V1\",\"mode\":\"REAL_SDK_DEPTH_OBSERVATION\",\"session\":"<<session<<",\"sequence\":"<<processed
                  <<",\"sdk_frame_index\":"<<f->index<<",\"dimensions\":[640,400],\"delivered_format\":\"Y16\",\"byte_order\":\"LITTLE_ENDIAN\",\"delivered_stride_bytes\":1280"
                  <<",\"raw_valid_pixels\":"<<raw_valid_pixels<<",\"raw_valid_ratio\":"<<double(raw_valid_pixels)/double(f->raw.size())<<",\"raw_zero_pixels\":"<<raw_zero_pixels<<",\"raw_nonzero_out_of_measurement_range_pixels\":"<<f->raw.size()-raw_zero_pixels-raw_valid_pixels<<",\"raw_valid_definition\":\"finite scaled depth within frozen [0.1,6.0]m measurement range\""
                  <<",\"sdk_value_scale_mm_per_count\":"<<f->scale_sdk<<",\"depth_scale_m_per_count\":"<<f->scale_m
                  <<",\"scale_source\":\"ob::DepthFrame::getValueScale() * 0.001 m/mm\",\"sdk_device_timestamp_us\":"<<f->device_us
                  <<",\"sdk_device_timestamp_source\":\"ob::Frame::timeStampUs()\",\"sdk_device_timestamp_domain\":\"SDK_FRAME_TIMESTAMP_UNMAPPED\""
                  <<",\"sdk_system_timestamp_us\":"<<f->system_us<<",\"sdk_system_timestamp_source\":\"ob::Frame::systemTimeStampUs()\",\"sdk_system_timestamp_domain\":\"SDK_SYSTEM_TIMESTAMP_UNMAPPED\""
                  <<",\"host_clock\":\"CLOCK_MONOTONIC\",\"host_wait_begin_ns\":"<<f->wait_begin
                  <<",\"host_wait_end_ns\":"<<f->wait_end<<",\"host_raw_copy_end_ns\":"<<f->copy_end<<",\"host_slot_publish_ns\":"<<f->published
                  <<",\"processing_begin_ns\":"<<processing_begin<<",\"processing_end_ns\":"<<processing_end
                  <<",\"decode_and_valid_ns\":"<<converted-processing_begin<<",\"offline_equivalent_core_pipeline_ns\":"<<processing_end-processing_begin
                  <<",\"host_delivery_to_result_ns\":"<<processing_end-f->wait_end
                  <<",\"exposure_monotonic_ns\":null,\"capture_age_ns\":null,\"clock_mapping\":\"BLOCKED\",\"live_freshness\":\"BLOCKED\""
                  <<",\"physical_output_permitted\":false,\"force_off\":1,\"control_valid\":0,";
            geometry_json(record,regions,counts,timing);record<<"}";
            return record.str();
}
inline void exclusive_evidence_file(const std::string& p) {
    const int fd=open(p.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);
    if(fd<0)throw std::runtime_error("EVIDENCE_OUTPUT_EXISTS_OR_NOT_WRITABLE");
    if(close(fd))throw std::runtime_error("EVIDENCE_EXCLUSIVE_CLOSE_FAILED");
}
struct FrameEvidenceFiles {
    std::string directory; uint64_t raw_limit=0,raw_samples_saved=0;
    std::ofstream frames,compact;
    FrameEvidenceFiles(const std::string& out,uint64_t samples):directory(out),raw_limit(samples) {
        exclusive_evidence_file(out+"/frames.jsonl");frames.open(out+"/frames.jsonl");
        exclusive_evidence_file(out+"/compact_frames.bin");compact.open(out+"/compact_frames.bin",std::ios::binary);
        if(!frames||!compact)throw std::runtime_error("EVIDENCE_STREAM_OPEN_FAILED");
    }
    void write(const FrameEvidenceRecord& e) {
        const auto json_record=render_frame_record(e);
        compact.write(reinterpret_cast<const char*>(e.compact_packet.data()),e.compact_packet.size());compact.flush();
        if(!compact)throw std::runtime_error("COMPACT_BINARY_LOG_WRITE_FAILED");
        frames<<json_record<<'\n';frames.flush();
        if(!frames)throw std::runtime_error("BOUNDED_FRAME_LOG_WRITE_FAILED");
        if(e.sequence<=raw_limit) {
            const auto p=directory+"/sample_"+std::to_string(e.sequence)+".y16";exclusive_evidence_file(p);
            std::ofstream raw(p,std::ios::binary);
            raw.write(reinterpret_cast<const char*>(e.frame.raw.data()),e.frame.raw.size()*sizeof(uint16_t));raw.flush();
            if(!raw)throw std::runtime_error("BOUNDED_SAMPLE_SAVE_FAILED");
            raw.close();if(raw.fail())throw std::runtime_error("BOUNDED_SAMPLE_CLOSE_FAILED");++raw_samples_saved;
        }
    }
};

