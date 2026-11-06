// Pure offline geometry acceleration candidate, corresponding to frozen V2.
// No frame clock claims, camera APIs, device files, FPGA or protocol output.
#include "gemini_geometry.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
#include <vector>
#include <atomic>
#include <thread>

namespace {
using Clock = std::chrono::steady_clock;
int64_t elapsed(Clock::time_point p) { return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-p).count(); }
struct RegionalPixels { int region; std::vector<int32_t> pixels; };
struct Witness {
    std::vector<RegionalPixels> regional;
    std::vector<int32_t> pixels;
    int slot, phase, zone, band, method;
    std::array<int32_t,4> core, bounds;
    double low, high, minimum, maximum;
};
struct Component { std::vector<int32_t> pixels; int first_block; };
struct BandComponent { int band; Component component; };
constexpr int DY[8]={-1,-1,-1,0,0,1,1,1};
constexpr int DX[8]={-1,0,1,-1,1,-1,0,1};

// For binary band components pixel vectors are sorted row-major, matching
// np.where(labels==label). Label-order parity is verified separately by Host.
std::vector<Component> cc8(const std::vector<uint8_t>& eligible,
                          const std::vector<double>& values, int h,int w,
                          bool depth_edges,double step) {
    const int n=h*w;
    std::vector<uint8_t> visited(n,0);
    std::vector<Component> out;
    const int bw=(w+1)/2;
    for(int seed=0;seed<n;++seed) {
        if(!eligible[seed]||visited[seed]) continue;
        Component c; c.first_block=std::numeric_limits<int>::max();
        c.pixels.push_back(seed); visited[seed]=1;
        for(size_t head=0;head<c.pixels.size();++head) {
            int p=c.pixels[head], y=p/w,x=p%w;
            c.first_block=std::min(c.first_block,(y/2)*bw+x/2);
            for(int k=0;k<8;++k) {
                int ny=y+DY[k],nx=x+DX[k];
                if(ny<0||ny>=h||nx<0||nx>=w) continue;
                int q=ny*w+nx;
                if(!eligible[q]||visited[q]) continue;
                if(depth_edges && std::abs(values[q]-values[p])>step+1e-12) continue;
                visited[q]=1;c.pixels.push_back(q);
            }
        }
        if(!depth_edges) std::sort(c.pixels.begin(),c.pixels.end());
        out.push_back(std::move(c));
    }
    // OpenCV's default CC8 implementation enumerates binary components on
    // 2x2 blocks. This changes diagnostic witness IDs only if a build differs;
    // the integration parity test also checks selected substantive metadata.
    if(!depth_edges) std::sort(out.begin(),out.end(),[](const Component&a,const Component&b){return a.first_block<b.first_block;});
    return out;
}

// Equivalent multi-label CC8: one tile scan per phase, rather than one scan
// for every distinct depth band. Components are subsequently ordered by the
// same band and OpenCV 2x2 label order as the original mask-per-band loop.
std::vector<BandComponent> band_components(const std::vector<uint8_t>& good,
        const std::vector<double>& values,int h,int w,double band_width,int phase) {
    const int n=h*w,bw=(w+1)/2;
    std::vector<int> bands(n,0);
    std::vector<uint8_t> visited(n,0);
    for(int p=0;p<n;++p)if(good[p])bands[p]=static_cast<int>(std::floor(values[p]/band_width+phase*0.5));
    std::vector<BandComponent> out;
    for(int seed=0;seed<n;++seed){
        if(!good[seed]||visited[seed])continue;
        BandComponent entry;entry.band=bands[seed];
        Component& c=entry.component;c.first_block=std::numeric_limits<int>::max();
        c.pixels.push_back(seed);visited[seed]=1;
        for(size_t head=0;head<c.pixels.size();++head){
            const int p=c.pixels[head],y=p/w,x=p%w;
            c.first_block=std::min(c.first_block,(y/2)*bw+x/2);
            for(int k=0;k<8;++k){
                const int ny=y+DY[k],nx=x+DX[k];
                if(ny<0||ny>=h||nx<0||nx>=w)continue;
                const int q=ny*w+nx;
                if(!good[q]||visited[q]||bands[q]!=entry.band)continue;
                visited[q]=1;c.pixels.push_back(q);
            }
        }
        std::sort(c.pixels.begin(),c.pixels.end());
        out.push_back(std::move(entry));
    }
    std::sort(out.begin(),out.end(),[](const BandComponent&a,const BandComponent&b){
        return a.band<b.band || (a.band==b.band&&a.component.first_block<b.component.first_block);
    });
    return out;
}

void add_witness(const std::vector<int32_t>& local, int tile_w,int x0,int y0,
                 std::array<int32_t,4> core,int slot,int phase,int zone,int band,int method,
                 const double* depth,const GFConfig& c,
                 std::vector<int32_t>& at,std::vector<Witness>& witnesses) {
    bool has_interior=false;
    Witness w;w.slot=slot;w.phase=phase;w.zone=zone;w.band=band;w.method=method;w.core=core;
    w.bounds={c.width,c.height,0,0};w.minimum=std::numeric_limits<double>::infinity();w.maximum=-w.minimum;
    w.pixels.reserve(local.size());
    for(int p:local) {
        int y=p/tile_w+y0,x=p%tile_w+x0;
        w.pixels.push_back(y*c.width+x);
        w.bounds[0]=std::min(w.bounds[0],x);w.bounds[1]=std::min(w.bounds[1],y);
        w.bounds[2]=std::max(w.bounds[2],x+1);w.bounds[3]=std::max(w.bounds[3],y+1);
        w.minimum=std::min(w.minimum,depth[y*c.width+x]);w.maximum=std::max(w.maximum,depth[y*c.width+x]);
        if(x>=core[0]&&x<core[2]&&y>=core[1]&&y<core[3]) has_interior=true;
    }
    if(!has_interior) return;
    const int side=c.height,center_left=(c.width-side)/2;
    for(int p:w.pixels){
        const int y=p/c.width,x=p%c.width;
        const int region=x<center_left?256:x>=center_left+side?257:
            (((y+1)*16-1)/side)*16+(((x-center_left+1)*16-1)/side);
        auto found=std::find_if(w.regional.begin(),w.regional.end(),[region](const RegionalPixels& item){return item.region==region;});
        if(found==w.regional.end()){w.regional.push_back({region,{}});found=w.regional.end()-1;}
        found->pixels.push_back(p);
    }
    w.low=(band-phase*0.5)*c.depth_band_m;
    w.high=(band+1-phase*0.5)*c.depth_band_m;
    int id=static_cast<int>(witnesses.size());
    const int frame_n=c.width*c.height;
    for(int p:w.pixels) {
        int y=p/c.width,x=p%c.width;
        if(x>=core[0]&&x<core[2]&&y>=core[1]&&y<core[3]) at[slot*frame_n+p]=id;
    }
    witnesses.push_back(std::move(w));
}

// Tiles are independent until the unsupported snapshot. Keep a separate
// witness vector per tile and disjoint core writes into at; merge strictly in
// the original raster order and offset all local witness IDs after join.
void build_one_tile(const double* depth,const uint8_t* valid,const GFConfig& c,
                    std::vector<int32_t>& at,std::vector<Witness>& ws,int left,int top) {
    const int bottom=std::min(c.height,top+c.tile_size);
            int right=std::min(c.width,left+c.tile_size);
            int x0=std::max(0,left-c.halo_pixels),x1=std::min(c.width,right+c.halo_pixels);
            int y0=std::max(0,top-c.halo_pixels),y1=std::min(c.height,bottom+c.halo_pixels);
            int tw=x1-x0,th=y1-y0,tn=tw*th;
            std::vector<double> values(tn);
            std::vector<uint8_t> good(tn,0);
            bool any=false;
            for(int y=0;y<th;++y) for(int x=0;x<tw;++x) {
                int p=y*tw+x,q=(y+y0)*c.width+x+x0;
                values[p]=depth[q];good[p]=valid[q];any=any||good[p];
            }
            if(!any)return;
            const std::array<int32_t,4> core={left,top,right,bottom};
            for(int phase=0;phase<2;++phase) {
                for(const auto& entry:band_components(good,values,th,tw,c.depth_band_m,phase)) {
                    int band=entry.band;
                    const auto& component=entry.component;
                        if(component.pixels.size()<static_cast<size_t>(c.component_min_pixels))continue;
                        add_witness(component.pixels,tw,x0,y0,core,phase,phase,-1,band,1,depth,c,at,ws);
                        std::array<std::vector<int32_t>,4> zones;
                        for(int p:component.pixels) {
                            double d=values[p];int zone=d<c.danger_m?0:d<c.caution_m?1:d<c.attention_max_m?2:3;
                            zones[zone].push_back(p);
                        }
                        int nonempty=0;for(auto&z:zones)nonempty+=!z.empty();
                        if(nonempty<=1)continue;
                        for(int zone=0;zone<4;++zone) if(!zones[zone].empty()) {
                            std::vector<uint8_t> zmask(tn,0);for(int p:zones[zone])zmask[p]=1;
                            for(const auto& sub:cc8(zmask,values,th,tw,false,c.adjacency_max_step_m))
                                if(sub.pixels.size()>=static_cast<size_t>(c.component_min_pixels))
                                    add_witness(sub.pixels,tw,x0,y0,core,phase+2,phase,zone,band,1,depth,c,at,ws);
                        }
                }
            }

}

std::vector<Witness> build(const double* depth,const uint8_t* valid,const GFConfig& c,
                           std::vector<int32_t>& at,GFStageTimes& times) {
    const auto start=Clock::now();
    const int n=c.width*c.height;
    std::vector<Witness> ws;
    const int tile_cols=(c.width+c.tile_size-1)/c.tile_size;
    const int tile_rows=(c.height+c.tile_size-1)/c.tile_size;
    const int tile_count=tile_cols*tile_rows;
    std::vector<std::vector<Witness>> tile_witnesses(tile_count);
    std::atomic<int> next_tile{0};
    const auto worker=[&](){
        for(;;){
            const int tile=next_tile.fetch_add(1,std::memory_order_relaxed);
            if(tile>=tile_count)return;
            const int left=(tile%tile_cols)*c.tile_size,top=(tile/tile_cols)*c.tile_size;
            build_one_tile(depth,valid,c,at,tile_witnesses[tile],left,top);
        }
    };
    const unsigned workers=std::min(4u,std::max(1u,std::thread::hardware_concurrency()));
    std::vector<std::thread> threads;threads.reserve(workers-1);
    for(unsigned i=1;i<workers;++i)threads.emplace_back(worker);
    worker();
    for(auto& thread:threads)thread.join();
    for(int tile=0;tile<tile_count;++tile){
        const int offset=static_cast<int>(ws.size());
        const int left=(tile%tile_cols)*c.tile_size,top=(tile/tile_cols)*c.tile_size;
        const int right=std::min(c.width,left+c.tile_size),bottom=std::min(c.height,top+c.tile_size);
        for(int slot=0;slot<4;++slot)
            for(int y=top;y<bottom;++y)for(int x=left;x<right;++x){
                int& id=at[slot*n+y*c.width+x];if(id>=0)id+=offset;
            }
        for(auto& w:tile_witnesses[tile])ws.push_back(std::move(w));
    }
    // Immutable unsupported snapshot before any fallback witness can lend support.
    std::vector<uint8_t> unsupported(n,0);
    bool any=false;
    for(int p=0;p<n;++p) {
        bool supported=false;for(int s=0;s<4;++s)supported=supported||at[s*n+p]>=0;
        unsupported[p]=valid[p]&&!supported;any=any||unsupported[p];
    }
    const auto adjacency_start=Clock::now();
    if(any) for(int top=0;top<c.height;top+=c.tile_size) {
        int bottom=std::min(c.height,top+c.tile_size);
        for(int left=0;left<c.width;left+=c.tile_size) {
            int right=std::min(c.width,left+c.tile_size);
            int x0=std::max(0,left-c.halo_pixels),x1=std::min(c.width,right+c.halo_pixels);
            int y0=std::max(0,top-c.halo_pixels),y1=std::min(c.height,bottom+c.halo_pixels);
            int tw=x1-x0,th=y1-y0;
            std::vector<double> values(tw*th);std::vector<uint8_t> mask(tw*th,0);bool has=false;
            for(int y=0;y<th;++y)for(int x=0;x<tw;++x){int p=y*tw+x,q=(y+y0)*c.width+x+x0;values[p]=depth[q];mask[p]=unsupported[q];has=has||mask[p];}
            if(!has)continue;
            for(const auto& component:cc8(mask,values,th,tw,true,c.adjacency_max_step_m))
                if(component.pixels.size()>=static_cast<size_t>(c.component_min_pixels))
                    add_witness(component.pixels,tw,x0,y0,{left,top,right,bottom},4,-1,-1,0,2,depth,c,at,ws);
        }
    }
    times.adjacency_ns=elapsed(adjacency_start);
    times.build_witness_ns=elapsed(start);
    return ws;
}

int quantize(double distance,const GFConfig&c){
    if(distance<=c.quantize_min_m+1e-6)return 4;
    if(distance>=c.attention_max_m)return 0;
    double ratio=(distance-c.quantize_min_m)/(c.attention_max_m-c.quantize_min_m);
    return std::min(4,std::max(1,static_cast<int>(std::floor(4-3*ratio+1e-6))));
}

void region(const double* depth,const uint8_t*valid,const GFConfig&c,
            const std::vector<int32_t>&at,const std::vector<Witness>&ws,
            std::array<int,4> bounds,int region_index,GFRegion& r,GFStageTimes&times) {
    std::memset(&r,0,sizeof(r));
    std::copy(bounds.begin(),bounds.end(),r.bounds);
    r.distance_m=std::numeric_limits<double>::quiet_NaN();r.witness_id=-1;r.phase_index=-1;r.risk_zone=-1;
    r.total_pixels=(bounds[2]-bounds[0])*(bounds[3]-bounds[1]);
    if(!r.total_pixels)return;
    const int n=c.width*c.height;
    std::vector<uint8_t> touched(ws.size(),0);
    for(int y=bounds[1];y<bounds[3];++y)for(int x=bounds[0];x<bounds[2];++x){
        int p=y*c.width+x;bool coherent=false;
        for(int s=0;s<5;++s){int id=at[s*n+p];if(id>=0){coherent=true;touched[id]=1;}}
        r.valid_pixels+=valid[p]!=0;r.coherent_pixels+=coherent;
        r.unsupported_near_pixels+=valid[p]&&depth[p]<c.attention_max_m&&!coherent;
    }
    r.coverage=static_cast<double>(r.valid_pixels)/r.total_pixels;
    r.coherent_coverage=static_cast<double>(r.coherent_pixels)/r.total_pixels;
    double best_distance=0;int best_support=0,best_y=0,best_x=0,best_id=-1;
    const auto candidate_start=Clock::now();
    for(size_t id=0;id<ws.size();++id)if(touched[id]){
        const auto&w=ws[id];
        const auto group=std::find_if(w.regional.begin(),w.regional.end(),[region_index](const RegionalPixels& item){return item.region==region_index;});
        if(group==w.regional.end())continue;
        const auto& pixels=group->pixels;std::vector<double> values;values.reserve(pixels.size());
        for(int p:pixels)values.push_back(depth[p]);
        if(values.empty())continue;
        // NumPy percentile(method='linear'): exact partition semantics with
        // _lerp's stable upper-half branch; no approximate histogram/rounding.
        double q=static_cast<double>(values.size()-1)*0.1;size_t lo=static_cast<size_t>(std::floor(q));double t=q-lo;
        std::vector<double> sorted=values;
        std::nth_element(sorted.begin(),sorted.begin()+lo,sorted.end());double a=sorted[lo],b=a;
        if(lo+1<sorted.size()){std::nth_element(sorted.begin()+lo+1,sorted.begin()+lo+1,sorted.end());b=sorted[lo+1];}
        double delta=b-a;double distance=t>=0.5?b-delta*(1.0-t):a+delta*t;
        size_t selected=0;double err=std::abs(values[0]-distance);
        for(size_t k=1;k<values.size();++k){double next=std::abs(values[k]-distance);if(next<err){selected=k;err=next;}}
        int y=pixels[selected]/c.width,x=pixels[selected]%c.width,support=static_cast<int>(pixels.size());
        bool better=best_id<0||distance<best_distance||(distance==best_distance&&(support>best_support||(support==best_support&&(y<best_y||(y==best_y&&x<best_x)))));
        if(better){best_distance=distance;best_support=support;best_y=y;best_x=x;best_id=static_cast<int>(id);}
    }
    times.candidate_percentile_ns+=elapsed(candidate_start);
    if(best_id<0)return;
    const auto&w=ws[best_id];r.has_candidate=1;r.distance_m=best_distance;r.uv[0]=best_x;r.uv[1]=best_y;
    r.quality=r.unsupported_near_pixels?0:(r.coverage>=c.complete_ratio&&r.coherent_coverage>=c.complete_ratio?1:2);
    r.component_support=static_cast<int>(w.pixels.size());r.region_support=best_support;r.level=quantize(best_distance,c);
    r.witness_id=best_id;r.phase_index=w.phase;r.risk_zone=w.zone;r.method=w.method;
    std::copy(w.core.begin(),w.core.end(),r.tile_core_bounds);std::copy(w.bounds.begin(),w.bounds.end(),r.witness_bounds);
    r.depth_band_low_m=w.low;r.depth_band_high_m=w.high;r.actual_depth_min_m=w.minimum;r.actual_depth_max_m=w.maximum;
    r.adjacency_max_step_m=c.adjacency_max_step_m;
}
}

GF_API int gf_geometry_abi_version(){return 1;}
GF_API int gf_geometry(const double*depth,const uint8_t*valid,const GFConfig*config,GFRegion*regions,GFCounts*counts,GFStageTimes*times){
    if(!depth||!valid||!config||!regions||!counts||!times)return -1;
    const GFConfig&c=*config;
    if(c.width<16||c.height<16||c.width>4096||c.height>4096||c.width<c.height||c.tile_size<4||c.halo_pixels<3||c.component_min_pixels<4||!std::isfinite(c.depth_band_m)||c.depth_band_m<=0||!std::isfinite(c.adjacency_max_step_m)||c.adjacency_max_step_m<=0)return -2;
    const auto total=Clock::now();std::memset(times,0,sizeof(*times));std::memset(counts,0,sizeof(*counts));
    const int n=c.width*c.height;
    for(int p=0;p<n;++p)if(valid[p]&&(!std::isfinite(depth[p])||depth[p]<c.measurement_min_m||depth[p]>c.measurement_max_m))return -3;
    std::vector<int32_t> at(5*n,-1);auto ws=build(depth,valid,c,at,*times);
    counts->witness_count=static_cast<int>(ws.size());
    for(int p=0;p<n;++p){bool supported=false;for(int s=0;s<5;++s)supported=supported||at[s*n+p]>=0;counts->coherent_pixels+=supported;counts->adjacency_pixels+=at[4*n+p]>=0;counts->accepted_pixels+=valid[p]!=0;counts->unsupported_near_pixels+=valid[p]&&depth[p]<c.attention_max_m&&!supported;}
    auto rs=Clock::now();int side=c.height,left=(c.width-side)/2;int index=0;
    std::array<std::array<int,4>,258> bounds;
    for(int row=0;row<16;++row)for(int col=0;col<16;++col)
        bounds[index++]={left+side*col/16,side*row/16,left+side*(col+1)/16,side*(row+1)/16};
    bounds[256]={0,0,left,c.height};bounds[257]={left+side,0,c.width,c.height};
    std::array<GFStageTimes,258> per_region_times{};
    std::atomic<int> next_region{0};
    const auto region_worker=[&](){
        for(;;){
            const int i=next_region.fetch_add(1,std::memory_order_relaxed);
            if(i>=258)return;
            region(depth,valid,c,at,ws,bounds[i],i,regions[i],per_region_times[i]);
        }
    };
    const unsigned region_workers=std::min(4u,std::max(1u,std::thread::hardware_concurrency()));
    std::vector<std::thread> region_threads;region_threads.reserve(region_workers-1);
    for(unsigned i=1;i<region_workers;++i)region_threads.emplace_back(region_worker);
    region_worker();
    for(auto& thread:region_threads)thread.join();
    // Sum of per-region measured candidate intervals across workers. It is
    // nested cumulative work and can exceed the region wall-clock duration.
    for(const auto& local:per_region_times)times->candidate_percentile_ns+=local.candidate_percentile_ns;
    times->region_ns=elapsed(rs);times->mapping_ns=0;times->total_ns=elapsed(total);
    return 0;
}
