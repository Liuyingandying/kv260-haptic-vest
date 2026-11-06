#pragma once
// Linux-only IPC, no SDK/device/MMIO operations. One pending application record;
// a worker owns at most one more record. Kernel send bytes are explicitly bounded.
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>

class LatestSeqpacketPublisher {
    static constexpr size_t max_bytes_ = 1048576;
    int fd_ = -1;
    std::mutex lock_;
    std::condition_variable changed_;
    std::string latest_, error_;
    bool has_latest_ = false, stopping_ = false;
    std::thread worker_;
    void work() noexcept {
        try {
            for (;;) {
                std::string value;
                {
                    std::unique_lock<std::mutex> guard(lock_);
                    changed_.wait(guard, [&] {return stopping_ || has_latest_;});
                    if (stopping_) return;
                    value.swap(latest_);has_latest_ = false;
                }
                for (;;) {
                    {
                        std::lock_guard<std::mutex> guard(lock_);
                        if (stopping_) return;
                        // Keep the newest complete unsent result, never append.
                        if (has_latest_) {value.swap(latest_);has_latest_=false;++worker_replaced;}
                    }
                    const auto n = send(fd_, value.data(), value.size(), MSG_DONTWAIT | MSG_NOSIGNAL);
                    if (n == static_cast<ssize_t>(value.size())) {++sent;break;}
                    if (n >= 0) throw std::runtime_error("SEQPACKET_PARTIAL_SEND");
                    if (errno == EINTR) continue;
                    if (errno != EAGAIN && errno != EWOULDBLOCK)
                        throw std::runtime_error(std::string("SEQPACKET_SEND_FAILED:")+std::strerror(errno));
                    ++would_block;
                    std::unique_lock<std::mutex> guard(lock_);
                    changed_.wait_for(guard, std::chrono::milliseconds(5), [&] {return stopping_ || has_latest_;});
                }
            }
        } catch (const std::exception& e) {
            std::lock_guard<std::mutex> guard(lock_);error_=e.what();stopping_=true;
            has_latest_=false;latest_.clear();changed_.notify_all();
        } catch (...) {
            std::lock_guard<std::mutex> guard(lock_);error_="UNKNOWN_IPC_WORKER_EXCEPTION";stopping_=true;
            has_latest_=false;latest_.clear();changed_.notify_all();
        }
    }
public:
    int actual_send_buffer_bytes = 0;
    std::atomic<uint64_t> offered{0},sent{0},slot_replaced{0},worker_replaced{0},would_block{0},discarded_on_close{0};
    explicit LatestSeqpacketPublisher(const std::string& path) {
        if (path.empty() || path.size() >= sizeof(sockaddr_un::sun_path))
            throw std::runtime_error("IPC_PATH_LENGTH_INVALID");
        struct stat st{};
        if (lstat(path.c_str(), &st) || !S_ISSOCK(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 0077))
            throw std::runtime_error("IPC_SOCKET_MUST_BE_PRIVATE_SAME_UID_SOCKET");
        fd_ = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd_ < 0) throw std::runtime_error("SEQPACKET_SOCKET_CREATE_FAILED");
        try {
            const int bound = 2097152;
            if (setsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &bound, sizeof(bound)))
                throw std::runtime_error("SEQPACKET_SET_SEND_BUFFER_FAILED");
            socklen_t actual_size=sizeof(actual_send_buffer_bytes);
            if (getsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &actual_send_buffer_bytes, &actual_size))
                throw std::runtime_error("SEQPACKET_GET_SEND_BUFFER_FAILED");
            sockaddr_un address{};address.sun_family=AF_UNIX;
            std::memcpy(address.sun_path,path.c_str(),path.size()+1);
            if (connect(fd_,reinterpret_cast<sockaddr*>(&address),sizeof(address))) {
                if (errno!=EINPROGRESS && errno!=EAGAIN) throw std::runtime_error("SEQPACKET_CONNECT_FAILED");
                pollfd poll_fd{fd_,POLLOUT,0};
                if (poll(&poll_fd,1,500)!=1) throw std::runtime_error("SEQPACKET_CONNECT_TIMEOUT_500MS");
                int error=0;socklen_t size=sizeof(error);
                if (getsockopt(fd_,SOL_SOCKET,SO_ERROR,&error,&size) || error)
                    throw std::runtime_error("SEQPACKET_CONNECT_SO_ERROR");
            }
            worker_=std::thread(&LatestSeqpacketPublisher::work,this);
        } catch (...) {close(fd_);fd_=-1;throw;}
    }
    LatestSeqpacketPublisher(const LatestSeqpacketPublisher&)=delete;
    LatestSeqpacketPublisher& operator=(const LatestSeqpacketPublisher&)=delete;
    void offer(std::string frame) {
        if (frame.empty() || frame.size()>max_bytes_) throw std::runtime_error("IPC_COMPLETE_FRAME_SIZE_INVALID");
        std::lock_guard<std::mutex> guard(lock_);
        if (stopping_) throw std::runtime_error(error_.empty()?"IPC_PUBLISHER_CLOSED":error_);
        ++offered;if (has_latest_) ++slot_replaced;
        latest_=std::move(frame);has_latest_=true;changed_.notify_one();
    }
    std::string error() {std::lock_guard<std::mutex> guard(lock_);return error_;}
    void stop() noexcept {
        {
            std::lock_guard<std::mutex> guard(lock_);
            stopping_=true;if(has_latest_)++discarded_on_close;has_latest_=false;latest_.clear();changed_.notify_all();
        }
        if (worker_.joinable()) worker_.join();
        if (fd_>=0) {shutdown(fd_,SHUT_RDWR);close(fd_);fd_=-1;}
    }
    ~LatestSeqpacketPublisher() {stop();}
};
