#include "compact_observation.hpp"
R6_COMPACT_API int r6_compact_encode(const GFConfig* c,const GFRegion* rs,const R6CompactMeta* m,uint8_t* out,char* error,uint32_t n) {
    if(!c||!m)return r6compact::reject(error,n,"NULL_METADATA");
    return r6compact::encode(*c,rs,*m,out,error,n);
}
