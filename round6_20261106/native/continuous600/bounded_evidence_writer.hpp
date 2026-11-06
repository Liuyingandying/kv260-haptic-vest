#pragma once
// Offline-capable lossless evidence queue. No SDK, IPC, device or MMIO access.
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

class BoundedEvidenceWriter {
public:
    struct Stats { uint64_t submitted=0,written=0,max_pending=0,backpressure=0; size_t pending=0; std::string error; };
private:
    const size_t capacity_;
    const bool asynchronous_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<std::function<void()>> queue_;
    std::thread worker_;
    bool closing_=false,running_=false;
    Stats stats_;
    void work() noexcept {
        for (;;) {
            std::function<void()> job;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                changed_.wait(lock,[&]{return closing_||!queue_.empty();});
                if(queue_.empty())return;
                job=std::move(queue_.front());queue_.pop_front();running_=true;
                changed_.notify_all();
            }
            try { job(); }
            catch(const std::exception& e) {std::lock_guard<std::mutex> lock(mutex_);stats_.error=e.what();running_=false;closing_=true;changed_.notify_all();return;}
            catch(...) {std::lock_guard<std::mutex> lock(mutex_);stats_.error="UNKNOWN_EVIDENCE_WRITE_FAILURE";running_=false;closing_=true;changed_.notify_all();return;}
            {
                std::lock_guard<std::mutex> lock(mutex_);++stats_.written;running_=false;changed_.notify_all();
            }
        }
    }
public:
    explicit BoundedEvidenceWriter(size_t capacity=2,bool asynchronous=true):capacity_(capacity),asynchronous_(asynchronous) {
        if(!capacity_)throw std::runtime_error("EVIDENCE_QUEUE_CAPACITY_ZERO");
        if(asynchronous_)worker_=std::thread(&BoundedEvidenceWriter::work,this);
    }
    BoundedEvidenceWriter(const BoundedEvidenceWriter&)=delete;
    BoundedEvidenceWriter& operator=(const BoundedEvidenceWriter&)=delete;
    void submit(std::function<void()> job) {
        if(!job)throw std::runtime_error("EMPTY_EVIDENCE_JOB");
        if(!asynchronous_) {
            {std::lock_guard<std::mutex> lock(mutex_);if(closing_)throw std::runtime_error("EVIDENCE_WRITER_CLOSED");++stats_.submitted;stats_.max_pending=1;}
            try {job();} catch(const std::exception& e) {std::lock_guard<std::mutex> lock(mutex_);stats_.error=e.what();closing_=true;throw;}
            std::lock_guard<std::mutex> lock(mutex_);++stats_.written;return;
        }
        std::unique_lock<std::mutex> lock(mutex_);
        if(queue_.size()>=capacity_)++stats_.backpressure;
        changed_.wait(lock,[&]{return closing_||!stats_.error.empty()||queue_.size()<capacity_;});
        if(!stats_.error.empty())throw std::runtime_error("EVIDENCE_WRITER_FAILED:"+stats_.error);
        if(closing_)throw std::runtime_error("EVIDENCE_WRITER_CLOSED");
        queue_.push_back(std::move(job));++stats_.submitted;
        const auto pending=queue_.size()+size_t(running_);
        if(pending>stats_.max_pending)stats_.max_pending=pending;
        changed_.notify_all();
    }
    // Drains every admitted job. A failed/incomplete drain can never return success.
    void finish() {
        {std::lock_guard<std::mutex> lock(mutex_);closing_=true;changed_.notify_all();}
        if(worker_.joinable())worker_.join();
        const auto s=stats();
        if(!s.error.empty())throw std::runtime_error("EVIDENCE_WRITER_FAILED:"+s.error);
        if(s.submitted!=s.written||s.pending)throw std::runtime_error("EVIDENCE_DRAIN_INCOMPLETE");
    }
    Stats stats() {std::lock_guard<std::mutex> lock(mutex_);auto s=stats_;s.pending=queue_.size()+size_t(running_);return s;}
    ~BoundedEvidenceWriter() {try{finish();}catch(...){} }
};
