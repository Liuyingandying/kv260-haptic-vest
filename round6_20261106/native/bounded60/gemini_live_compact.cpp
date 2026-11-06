// Independent R4 SDK capture -> unchanged optimized parallel_v2 geometry; atomic IPC.
// No hardware execution is authorized by compilation. No freshness mapping is fabricated.
// Never opens UIO/devmem/I2C. The diagnostic matrix is not a control permit.
#include "geometry_diagnostic.hpp"
#include "latest_seqpacket.hpp"
#include "raw_sha256.hpp"
#include "compact_observation.hpp"
#include "bounded_evidence_writer.hpp"
#include "frame_evidence.hpp"
#include "libobsensor/ObSensor.hpp"
#include <atomic>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <thread>
#include <unistd.h>

namespace {
uint64_t mono(){timespec t{};if(clock_gettime(CLOCK_MONOTONIC,&t))throw std::runtime_error("CLOCK_MONOTONIC_FAILED");return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec;}
volatile sig_atomic_t interrupted=0;
void signal_stop(int){interrupted=1;}
void require(bool ok,const char* error){if(!ok)throw std::runtime_error(error);}
std::string quoted(const std::string& s){std::ostringstream o;o<<'"';for(unsigned char c:s){if(c=='"'||c=='\\')o<<'\\'<<char(c);else if(c=='\n')o<<"\\n";else if(c=='\r')o<<"\\r";else if(c=='\t')o<<"\\t";else if(c<32)o<<'?';else o<<char(c);}return o.str()+'"';}
std::string sdk_error(const ob::Error& e){return std::string(e.getMessage()?e.getMessage():"SDK_ERROR");}
std::array<uint8_t,32> digest_bytes(const std::string& text){
    require(text.size()==64,"HEX_DIGEST_LENGTH");std::array<uint8_t,32> result{};
    for(unsigned i=0;i<32;++i){const auto pair=text.substr(2*i,2);for(char c:pair)require((c>='0'&&c<='9')||(c>='a'&&c<='f'),"HEX_DIGEST_CHAR");result[i]=uint8_t(std::stoul(pair,nullptr,16));}
    return result;
}
void exclusive_file(const std::string& p){int fd=open(p.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);require(fd>=0,"OUTPUT_ALREADY_EXISTS_OR_NOT_WRITABLE");close(fd);}
struct Log {
    std::ofstream out;std::mutex lock;
    explicit Log(const std::string& p){exclusive_file(p);out.open(p);require(bool(out),"METADATA_OPEN_FAILED");}
    void phase(const char* name,const char* result,const std::string& error=""){
        std::lock_guard<std::mutex> guard(lock);
        out<<"{\"schema\":\"GEMINI_NATIVE_PHASE_V1\",\"host_monotonic_ns\":"<<mono()
           <<",\"phase\":"<<quoted(name)<<",\"result\":"<<quoted(result)<<",\"error\":"<<quoted(error)<<"}\n";out.flush();
    }
};
using Frame=EvidenceFrame;
struct Slot {
    std::mutex mutex;std::condition_variable changed;std::unique_ptr<Frame> latest;
    bool done=false;std::string error;uint64_t captured=0,replaced=0,timeouts=0,duplicate=0,out_of_order=0;
};
struct Sdk {
    std::unique_ptr<ob::Context> context;std::shared_ptr<ob::DeviceList> devices;
    std::shared_ptr<ob::Device> device;std::shared_ptr<ob::DeviceInfo> info;
    std::unique_ptr<ob::Pipeline> pipeline;std::shared_ptr<ob::StreamProfileList> profiles;
    std::shared_ptr<ob::VideoStreamProfile> profile;std::shared_ptr<ob::Config> config;
};
void capture_thread(Sdk& sdk,Slot& slot,Log& log,std::atomic<bool>& stop,uint64_t goal,uint64_t deadline){
    uint64_t previous=0;bool have_previous=false;unsigned consecutive=0;
    try{
        while(!stop.load()&&!interrupted&&mono()<deadline){
            auto f=std::make_unique<Frame>();f->wait_begin=mono();
            auto set=sdk.pipeline->waitForFrames(1000);f->wait_end=mono();
            auto depth=set?set->depthFrame():nullptr;
            if(!depth){++slot.timeouts;if(++consecutive>=3)throw std::runtime_error("THREE_CONSECUTIVE_FRAME_TIMEOUTS");continue;}
            consecutive=0;
            require(depth->type()==OB_FRAME_DEPTH&&depth->format()==OB_FORMAT_Y16,"DELIVERED_FRAME_NOT_DEPTH_Y16");
            require(depth->width()==640&&depth->height()==400&&depth->dataSize()==640*400*2&&depth->data(),"DELIVERED_FRAME_GEOMETRY_INVALID");
            f->index=depth->index();f->device_us=depth->timeStampUs();f->system_us=depth->systemTimeStampUs();
            f->scale_sdk=depth->getValueScale();f->scale_m=f->scale_sdk*0.001;
            require(std::isfinite(f->scale_m)&&f->scale_m>0,"SDK_DEPTH_SCALE_INVALID");
            // Exact contiguous Y16 delivered by SDK. No assumed native sensor row padding.
            f->raw.resize(640*400);std::memcpy(f->raw.data(),depth->data(),640*400*2);f->copy_end=mono();
            if(have_previous&&f->index<=previous){if(f->index==previous)++slot.duplicate;else ++slot.out_of_order;log.phase("FRAME_SEQUENCE","REJECT","DUPLICATE_OR_OUT_OF_ORDER");continue;}
            previous=f->index;have_previous=true;
            f->published=mono();
            {std::lock_guard<std::mutex> g(slot.mutex);++slot.captured;if(slot.latest)++slot.replaced;slot.latest=std::move(f);}
            slot.changed.notify_one();
            if(slot.captured>=goal)break;
        }
        if(!stop.load()&&!interrupted&&slot.captured==0)slot.error="SESSION_DURATION_EXPIRED_WITHOUT_FRAME";
    }catch(const ob::Error& e){slot.error=sdk_error(e);log.phase("CAPTURE_THREAD","FAIL",slot.error);}
    catch(const std::exception& e){slot.error=e.what();log.phase("CAPTURE_THREAD","FAIL",slot.error);}
    catch(...){slot.error="UNKNOWN_CAPTURE_EXCEPTION";log.phase("CAPTURE_THREAD","FAIL",slot.error);}
    {std::lock_guard<std::mutex> g(slot.mutex);slot.done=true;}slot.changed.notify_all();
}
}
int main(int argc,char** argv){
    std::cout.setf(std::ios::unitbuf);std::cerr.setf(std::ios::unitbuf);
    std::cout<<"GEMINI_COMPACT_NATIVE_MAIN_BEGIN\n";
    if(argc!=8){std::cerr<<"usage: gemini_live_ipc SDK_CONFIG_XML EXISTING_NEW_OUTPUT_DIR FRAME_LIMIT SESSION_U32 SAMPLE_RAW_COUNT(0..64) PRIVATE_UDS_PATH DURATION_MS(1..120000)\n";return 64;}
    const std::string xml=argv[1],out=argv[2],ipc_path=argv[6];uint64_t goal=0,session=0,samples=0,duration_ms=0;
    try{goal=std::stoull(argv[3]);session=std::stoull(argv[4]);samples=std::stoull(argv[5]);duration_ms=std::stoull(argv[7]);require(goal>=1&&goal<=1800&&session>=1&&session<=UINT32_MAX&&samples<=64&&duration_ms>=1&&duration_ms<=120000,"INVALID_BOUNDED_ARGUMENTS");}
    catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 64;}
    struct stat st{};if(stat(out.c_str(),&st)||!S_ISDIR(st.st_mode)){std::cerr<<"OUTPUT_DIRECTORY_REQUIRED\n";return 64;}
    signal(SIGTERM,signal_stop);signal(SIGINT,signal_stop);
    std::unique_ptr<Log> logger;
    try{logger=std::make_unique<Log>(out+"/metadata.jsonl");}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 65;}
    Log& log=*logger;Sdk sdk;Slot slot;std::atomic<bool> stop{false};std::thread producer;
    bool start_attempted=false,start_returned=false,stop_attempted=false,stop_returned=false,cleanup_completed=false;
    std::unique_ptr<LatestSeqpacketPublisher> ipc;
    std::unique_ptr<BoundedEvidenceWriter> evidence;
    std::shared_ptr<FrameEvidenceFiles> evidence_files;
    BoundedEvidenceWriter::Stats evidence_stats;
    std::vector<uint64_t> evidence_admit_times;
    std::string error;uint64_t processed=0,began=mono(),capture_session_started=0;std::vector<uint64_t> core_times,total_times,delivery_ages;
    try{
        log.phase("ENTRY","PASS");const uint16_t endian=1;require(*reinterpret_cast<const uint8_t*>(&endian)==1,"LITTLE_ENDIAN_REQUIRED");
        utsname u{};require(uname(&u)==0&&std::string(u.machine)=="aarch64"&&std::string(u.sysname)=="Linux","LINUX_AARCH64_REQUIRED");
        const std::string version=std::to_string(ob::Version::getMajor())+"."+std::to_string(ob::Version::getMinor())+"."+std::to_string(ob::Version::getPatch());
        require(version=="1.10.37","SDK_VERSION_MUST_BE_1_10_37");require(gf_geometry_abi_version()==1,"GEOMETRY_ABI_MISMATCH");
        log.phase("IPC_CONNECT_BEGIN","BEGIN");ipc=std::make_unique<LatestSeqpacketPublisher>(ipc_path);log.phase("IPC_CONNECT_END","PASS_NO_SDK_STARTED_YET");
        log.phase("CONTEXT_CREATE_BEGIN","BEGIN");sdk.context=std::make_unique<ob::Context>(xml.c_str());log.phase("CONTEXT_CREATE_END","PASS");
        sdk.devices=sdk.context->queryDeviceList();require(bool(sdk.devices),"NO_DEVICE_LIST");unsigned matches=0,selected=0;
        for(unsigned i=0;i<sdk.devices->deviceCount();++i)if(sdk.devices->vid(i)==0x2bc5&&sdk.devices->pid(i)==0x0614){++matches;selected=i;}
        require(matches==1,"EXACTLY_ONE_2BC5_0614_REQUIRED");log.phase("DEVICE_OPEN_BEGIN","BEGIN");
        sdk.device=sdk.devices->getDevice(selected);sdk.info=sdk.device->getDeviceInfo();log.phase("DEVICE_OPEN_END","PASS");
        require(sdk.info->vid()==0x2bc5&&sdk.info->pid()==0x0614,"OPENED_DEVICE_IDENTITY_MISMATCH");
        std::string identity_text="2bc5:0614";
        identity_text.push_back(0);identity_text+=(sdk.info->uid()?sdk.info->uid():"");
        identity_text.push_back(0);identity_text+=(sdk.info->serialNumber()?sdk.info->serialNumber():"");
        identity_text.push_back(0);identity_text+=(sdk.info->firmwareVersion()?sdk.info->firmwareVersion():"");
        require(sdk.info->uid()&&*sdk.info->uid(),"ACTUAL_DEVICE_UID_REQUIRED");
        const std::string identity_sha=r6_sha256(identity_text.data(),identity_text.size());
        const auto identity_bytes=digest_bytes(identity_sha);
        exclusive_file(out+"/compact_identity.json");
        {std::ofstream identity(out+"/compact_identity.json");identity<<"{\"schema\":\"G6C1_DEVICE_BINDING_V1\",\"vid\":11205,\"pid\":1556,\"uid\":"<<quoted(sdk.info->uid())<<",\"serial\":"<<(sdk.info->serialNumber()?quoted(sdk.info->serialNumber()):"null")<<",\"firmware\":"<<(sdk.info->firmwareVersion()?quoted(sdk.info->firmwareVersion()):"null")<<",\"sha256\":"<<quoted(identity_sha)<<"}\n";identity.flush();require(bool(identity),"COMPACT_IDENTITY_SAVE_FAILED");}
        sdk.pipeline=std::make_unique<ob::Pipeline>(sdk.device);sdk.profiles=sdk.pipeline->getStreamProfileList(OB_SENSOR_DEPTH);
        int best=4;for(unsigned i=0;i<sdk.profiles->count();++i){auto p=sdk.profiles->getProfile(i)->as<ob::VideoStreamProfile>();if(p->width()!=640||p->height()!=400||p->fps()!=15)continue;int rank=p->format()==OB_FORMAT_Y16?0:p->format()==OB_FORMAT_Y11?1:p->format()==OB_FORMAT_Y12?2:3;if(rank<best&&rank<3){best=rank;sdk.profile=p;}}
        require(bool(sdk.profile),"NO_SUPPORTED_640X400_15_PROFILE");sdk.config=std::make_shared<ob::Config>();sdk.config->disableAllStream();sdk.config->enableStream(sdk.profile);
        // Cache immutable device strings before the worker starts; it never touches SDK objects.
        std::ostringstream identity_record;
        identity_record<<"{\"vid\":"<<sdk.info->vid()<<",\"pid\":"<<sdk.info->pid()<<",\"uid\":"<<quoted(sdk.info->uid()?sdk.info->uid():"")<<",\"serial\":"<<(sdk.info->serialNumber()?quoted(sdk.info->serialNumber()):"null")<<",\"name\":"<<(sdk.info->name()?quoted(sdk.info->name()):"null")<<",\"firmware\":"<<(sdk.info->firmwareVersion()?quoted(sdk.info->firmwareVersion()):"null")<<",\"connection_type\":"<<(sdk.info->connectionType()?quoted(sdk.info->connectionType()):"null")<<"}";
        const auto device_identity_json=identity_record.str();
        evidence_files=std::make_shared<FrameEvidenceFiles>(out,samples);
#ifdef R6_SYNC_EVIDENCE_BASELINE
        evidence=std::make_unique<BoundedEvidenceWriter>(2,false);
#else
        evidence=std::make_unique<BoundedEvidenceWriter>(2,true);
#endif
        capture_session_started=mono();
        log.phase("DEPTH_START_BEGIN","BEGIN");start_attempted=true;sdk.pipeline->start(sdk.config);start_returned=true;log.phase("DEPTH_START_END","PASS");
        const uint64_t deadline=mono()+duration_ms*1000000ULL;
        producer=std::thread(capture_thread,std::ref(sdk),std::ref(slot),std::ref(log),std::ref(stop),goal,deadline);
        const GFConfig config=frozen_gemini_config();std::vector<double> meters;std::vector<uint8_t> valid;
        while(!interrupted){
            std::unique_ptr<Frame> f;{std::unique_lock<std::mutex> g(slot.mutex);slot.changed.wait_for(g,std::chrono::milliseconds(1200),[&]{return slot.latest||slot.done;});if(slot.latest)f=std::move(slot.latest);else if(slot.done)break;}
            if(!f)continue;
            const uint64_t raw_hash_begin=mono();
            const std::string raw_sha256=r6_sha256(f->raw.data(),f->raw.size()*sizeof(uint16_t));
            const uint64_t raw_hash_end=mono();
            const uint64_t processing_begin=mono();decode_y16(f->raw,f->scale_m,config,meters,valid);const uint64_t converted=mono();
            std::array<GFRegion,258> regions{};GFCounts counts{};GFStageTimes timing{};
            require(gf_geometry(meters.data(),valid.data(),&config,regions.data(),&counts,&timing)==0,"NATIVE_GEOMETRY_FAILED");
            const auto raw_valid_pixels=std::count(valid.begin(),valid.end(),uint8_t(1));
            const auto raw_zero_pixels=std::count(f->raw.begin(),f->raw.end(),uint16_t(0));
            const uint64_t processing_end=mono();++processed;core_times.push_back(timing.total_ns);total_times.push_back(processing_end-processing_begin);delivery_ages.push_back(processing_end-f->wait_end);
            R6CompactMeta compact_meta{};compact_meta.session=uint32_t(session);compact_meta.sequence=processed;
            compact_meta.sdk_index=f->index;compact_meta.session_started_ns=capture_session_started;
            compact_meta.wait_begin_ns=f->wait_begin;compact_meta.wait_end_ns=f->wait_end;
            compact_meta.processing_begin_ns=processing_begin;compact_meta.processing_end_ns=processing_end;
            compact_meta.encoding_begin_ns=mono();compact_meta.sdk_device_us=f->device_us;compact_meta.sdk_system_us=f->system_us;
            compact_meta.scale_m=f->scale_m;compact_meta.scale_sdk_mm=f->scale_sdk;
            compact_meta.raw_valid_pixels=uint32_t(raw_valid_pixels);compact_meta.raw_zero_pixels=uint32_t(raw_zero_pixels);
            compact_meta.raw_out_of_range_pixels=uint32_t(f->raw.size()-raw_zero_pixels-raw_valid_pixels);
            const auto raw_digest_bytes=digest_bytes(raw_sha256);
            std::memcpy(compact_meta.raw_sha256,raw_digest_bytes.data(),32);
            std::memcpy(compact_meta.identity_sha256,identity_bytes.data(),32);
            std::array<uint8_t,512> compact_packet{};char compact_error[160]{};
            if(r6compact::encode(config,regions.data(),compact_meta,compact_packet.data(),compact_error,sizeof(compact_error)))
                throw std::runtime_error(std::string("COMPACT_STRICT_VALIDATION_FAILED:")+compact_error);
            const auto admit_begin=mono();
            auto audit=std::make_shared<FrameEvidenceRecord>();
            audit->frame=std::move(*f);audit->device_identity_json=device_identity_json;audit->raw_sha256=raw_sha256;
            audit->sequence=processed;audit->session=session;audit->capture_session_started=capture_session_started;
            audit->raw_hash_ns=raw_hash_end-raw_hash_begin;audit->processing_begin=processing_begin;
            audit->converted=converted;audit->processing_end=processing_end;
            audit->raw_valid_pixels=raw_valid_pixels;audit->raw_zero_pixels=raw_zero_pixels;
            audit->regions=regions;audit->counts=counts;audit->timing=timing;audit->compact_packet=compact_packet;
            // Admission precedes offer, never drops a job, and blocks on the two-slot bound.
            // A write error/incomplete final drain makes this entire native session fail.
            // Enqueue is NOT disk durability; acceptance must require the final drain and raw budget.
            evidence->submit([files=evidence_files,audit]{files->write(*audit);});
            evidence_admit_times.push_back(mono()-admit_begin);
            ipc->offer(std::string(reinterpret_cast<const char*>(compact_packet.data()),compact_packet.size()));
        }
        stop=true;if(producer.joinable())producer.join();if(!slot.error.empty())throw std::runtime_error(slot.error);
        require(processed>0,"NO_PROCESSED_FRAME");require(!interrupted,"INTERRUPTED");
    }catch(const ob::Error& e){error=sdk_error(e);log.phase("SESSION","FAIL",error);}
    catch(const std::exception& e){error=e.what();log.phase("SESSION","FAIL",error);}
    catch(...){error="UNKNOWN_SESSION_EXCEPTION";log.phase("SESSION","FAIL",error);}
    stop=true;if(producer.joinable())producer.join();
    if(evidence) {
        try {evidence->finish();log.phase("EVIDENCE_WRITER_DRAIN","PASS");}
        catch(const std::exception& e){log.phase("EVIDENCE_WRITER_DRAIN","FAIL",e.what());if(error.empty())error=e.what();}
        evidence_stats=evidence->stats();
    }
    // Application capture thread is joined before SDK stop. SDK internals remain opaque.
    log.phase("APPLICATION_CAPTURE_THREAD_JOIN","PASS");
    if(ipc){ipc->stop();log.phase("IPC_WORKER_JOIN","PASS");if(error.empty()&&!ipc->error().empty())error=ipc->error();}
    if(start_attempted&&sdk.pipeline){stop_attempted=true;log.phase("STOP_REQUESTED","BEGIN");try{sdk.pipeline->stop();stop_returned=true;log.phase("STOP_RETURNED","PASS");}catch(const ob::Error& e){log.phase("STOP_RETURNED","FAIL",sdk_error(e));if(error.empty())error="SDK_STOP_EXCEPTION";}catch(const std::exception& e){log.phase("STOP_RETURNED","FAIL",e.what());if(error.empty())error="SDK_STOP_EXCEPTION";}catch(...){log.phase("STOP_RETURNED","FAIL","UNKNOWN_STOP_EXCEPTION");if(error.empty())error="SDK_STOP_EXCEPTION";}}
    log.phase("SDK_INTERNAL_THREADS_WAIT","UNKNOWN");log.phase("RESOURCE_DESTRUCT_BEGIN","BEGIN");
    sdk.config.reset();sdk.profile.reset();sdk.profiles.reset();sdk.pipeline.reset();sdk.info.reset();sdk.device.reset();sdk.devices.reset();sdk.context.reset();cleanup_completed=true;
    log.phase("CLEANUP_COMPLETED","PASS");log.phase("RESOURCE_RECOVERY_UNKNOWN","UNKNOWN");
    const int code=error.empty()?0:70;
    const auto summarize=[](const std::vector<uint64_t>& v){std::ostringstream o;o<<'[';for(size_t i=0;i<v.size();++i){if(i)o<<',';o<<v[i];}return o.str()+']';};
    exclusive_file(out+"/session_result.json");std::ofstream result(out+"/session_result.json");
    result<<"{\"schema\":\"GEMINI_NATIVE_SESSION_V1\",\"pid\":"<<getpid()<<",\"session\":"<<session<<",\"goal_capture_frames\":"<<goal
      <<",\"captured_frames\":"<<slot.captured<<",\"processed_frames\":"<<processed<<",\"latest_slot_replacements\":"<<slot.replaced
      <<",\"timeouts\":"<<slot.timeouts<<",\"duplicate_rejected\":"<<slot.duplicate<<",\"out_of_order_rejected\":"<<slot.out_of_order
      <<",\"start_attempted\":"<<(start_attempted?"true":"false")<<",\"start_returned\":"<<(start_returned?"true":"false")
      <<",\"stop_attempted\":"<<(stop_attempted?"true":"false")<<",\"stop_returned\":"<<(stop_returned?"true":"false")
      <<",\"cleanup_completed\":"<<(cleanup_completed?"true":"false")<<",\"sdk_warning_observed\":\"SUPERVISOR_RAW_LOG_AUDIT_REQUIRED\""
      <<",\"resource_recovery\":\"UNKNOWN\",\"clock_mapping\":\"BLOCKED\",\"live_freshness\":\"BLOCKED\""
      <<",\"ipc_actual_send_buffer_bytes\":"<<(ipc?ipc->actual_send_buffer_bytes:0)
      <<",\"ipc_offered\":"<<(ipc?ipc->offered.load():0)<<",\"ipc_sent\":"<<(ipc?ipc->sent.load():0)<<",\"ipc_slot_replaced\":"<<(ipc?ipc->slot_replaced.load():0)<<",\"ipc_worker_replaced\":"<<(ipc?ipc->worker_replaced.load():0)<<",\"ipc_would_block\":"<<(ipc?ipc->would_block.load():0)<<",\"ipc_discarded_on_close\":"<<(ipc?ipc->discarded_on_close.load():0)<<",\"audit_full_record_limit\":1800"
      <<",\"evidence_queue_capacity\":2,\"evidence_max_pending\":"<<evidence_stats.max_pending
      <<",\"evidence_submitted\":"<<evidence_stats.submitted<<",\"evidence_written\":"<<evidence_stats.written<<",\"evidence_pending\":"<<evidence_stats.pending
      <<",\"evidence_backpressure\":"<<evidence_stats.backpressure<<",\"evidence_error\":"<<quoted(evidence_stats.error)
      <<",\"raw_samples_requested\":"<<samples<<",\"raw_samples_saved\":"<<(evidence_files?evidence_files->raw_samples_saved:0)
      <<",\"full_raw_evidence\":"<<((evidence_files&&evidence_stats.written==processed&&evidence_files->raw_samples_saved==processed)?"true":"false")
      <<",\"evidence_admit_ns\":"<<summarize(evidence_admit_times)
      <<",\"force_off\":1,\"control_valid\":0,\"native_total_ns\":"<<summarize(core_times)<<",\"core_pipeline_ns\":"<<summarize(total_times)
      <<",\"delivery_to_result_ns\":"<<summarize(delivery_ages)<<",\"session_wall_ns\":"<<mono()-began<<",\"error\":"<<quoted(error)<<",\"exit_code\":"<<code<<"}\n";
    log.phase("PROGRAM_EXIT",code?"FAIL":"PASS",error);std::cout<<"GEMINI_NATIVE_PROGRAM_EXIT code="<<code<<'\n';return code;
}
