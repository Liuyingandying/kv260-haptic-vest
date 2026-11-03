/* Isolated KV260 recovery diagnostic. Internal AXI only; no external IO.
 * Standard GPIO control 0x9e000000; frozen payload 0x9f000000.
 * No PS writes, Flash/QSPI, reset commands, or automatic retries.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>



struct window{int fd;volatile uint32_t *p;};
static void emit(int fd,const char *s,size_t n){size_t p=0;while(p<n){ssize_t k=write(fd,s+p,n-p);if(k<0&&errno==EINTR)continue;if(k<=0)_exit(90);p+=(size_t)k;}}
static void logline(const char *fmt,...){char b[1024];va_list a;va_start(a,fmt);int n=vsnprintf(b,sizeof b,fmt,a);va_end(a);if(n<=0||(size_t)n>=sizeof b)_exit(91);emit(2,b,(size_t)n);}
static void require(int ok,const char *why){if(!ok){logline("RECOVERY_ERROR %s errno=%d\n",why,errno);exit(20);}}
static int text(const char *p,char *b,size_t n){int fd=open(p,O_RDONLY|O_CLOEXEC);if(fd<0)return 0;ssize_t k=read(fd,b,n-1);int z=close(fd);if(k<=0||k==(ssize_t)n-1||z)return 0;b[k]=0;b[strcspn(b,"\r\n")]=0;return 1;}
static void discover(const char *expected,uint64_t base,char *dev,size_t cap){DIR *d=opendir("/sys/class/uio");require(d!=NULL,"open sysfs UIO");struct dirent *e;unsigned matches=0;while((e=readdir(d))){if(strncmp(e->d_name,"uio",3)||strlen(e->d_name)>24)continue;char p[512],b[256];snprintf(p,sizeof p,"/sys/class/uio/%s/name",e->d_name);if(!text(p,b,sizeof b)||strcmp(b,expected))continue;snprintf(p,sizeof p,"/sys/class/uio/%s/maps/map0/addr",e->d_name);require(text(p,b,sizeof b),"UIO addr");require(strtoull(b,NULL,16)==base,"UIO base mismatch");snprintf(p,sizeof p,"/sys/class/uio/%s/maps/map0/size",e->d_name);require(text(p,b,sizeof b)&&strtoull(b,NULL,16)==65536,"UIO size mismatch");snprintf(p,sizeof p,"/sys/class/uio/%s/maps/map0/offset",e->d_name);require(text(p,b,sizeof b)&&strtoull(b,NULL,16)==0,"UIO offset mismatch");int n=snprintf(dev,cap,"/dev/%s",e->d_name);require(n>0&&(size_t)n<cap,"UIO path length");matches++;}require(closedir(d)==0&&matches==1,"UIO identity not unique");logline("UIO_IDENTITY name=%s base=0x%08" PRIx64 " path=%s\n",expected,base,dev);}
static void afi_guard(void){int f=open("/dev/mem",O_RDONLY|O_SYNC|O_CLOEXEC);require(f>=0,"open PS AFI read only");void *p=mmap(NULL,4096,PROT_READ,MAP_SHARED,f,(off_t)UINT64_C(0xff419000));require(p!=MAP_FAILED,"map PS AFI read only");uint32_t a=*(volatile const uint32_t *)p;logline("AFI_POST_UIO_OPEN=0x%08x EXPECTED=0x00000200\n",(unsigned)a);require(munmap(p,4096)==0&&close(f)==0,"close PS AFI");require(a==0x200,"AFI does not match diagnostic 128-bit design; no PL access");}
static void close_window(struct window *w){require(munmap((void *)w->p,65536)==0,"unmap UIO");require(close(w->fd)==0,"close UIO");w->p=NULL;logline("UIO_CLOSED\n");}
static uint32_t rd(struct window *w,unsigned off){require(off<65536&&!(off&3),"read offset");__asm__ __volatile__("dmb sy":::"memory");uint32_t v=w->p[off/4];__asm__ __volatile__("dmb sy":::"memory");return v;}
static void wr(struct window *w,unsigned off,uint32_t v){require(off<65536&&!(off&3),"write offset");__asm__ __volatile__("dmb sy":::"memory");w->p[off/4]=v;__asm__ __volatile__("dsb sy":::"memory");}

static uint64_t mono_ns(void){struct timespec t;require(!clock_gettime(CLOCK_MONOTONIC,&t),"monotonic");return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static struct window map_live(const char *name,uint64_t base,int writeable){char dev[80];discover(name,base,dev,sizeof dev);int f=open(dev,(writeable?O_RDWR:O_RDONLY)|O_SYNC|O_CLOEXEC);require(f>=0,"UIO open");void *p=mmap(NULL,65536,PROT_READ|(writeable?PROT_WRITE:0),MAP_SHARED,f,0);require(p!=MAP_FAILED,"UIO map");afi_guard();return(struct window){f,p};}
static struct window diag,mb,iic;
static uint32_t safe_status(void){
 require(rd(&diag,0)==0x4b493031,"configuration identity changed");
 uint32_t d=rd(&diag,8);require((d&44)==44&&!(d&0x100)&&rd(&diag,12)==0xffffffffu,"reset/pad guard");
 require(rd(&iic,0x100)==0&&rd(&iic,0x28)==0&&rd(&iic,0x1c)==0,"IIC must remain disabled");
 uint32_t s=rd(&mb,4);require((s&12)==8,"physical control not inhibited");return s;
}
static void snapshot(const char *kind){uint32_t s=safe_status();printf("{\"kind\":\"%s\",\"status\":%u,\"generation\":%u,\"reason\":%u,\"active_session\":%u,\"active_sequence\":%" PRIu64 ",\"capture_ns\":%" PRIu64 ",\"pl_age_us\":%u,\"now_ns\":%" PRIu64 ",\"iic_disabled\":true,\"pad_disarmed\":true}\n",kind,s,rd(&mb,8),rd(&mb,12),rd(&mb,16),(uint64_t)rd(&mb,20)|((uint64_t)rd(&mb,24)<<32),(uint64_t)rd(&mb,0x418)|((uint64_t)rd(&mb,0x41c)<<32),rd(&mb,32),mono_ns());}
static int input(void *data,size_t count,int eof_ok){size_t n=0;while(n<count){struct pollfd p={.fd=0,.events=POLLIN};int k=poll(&p,1,15000);if(k<0&&errno==EINTR)continue;require(k>0,"bounded stdin wait");ssize_t z=read(0,(char*)data+n,count-n);if(z<0&&errno==EINTR)continue;if(z==0&&n==0&&eof_ok)return 0;require(z>0,"short input");n+=(size_t)z;}return 1;}
static void submit(uint32_t *in){
 uint32_t s=safe_status(),before=rd(&mb,8),prior[96];
 require(!(s&0x11),"mailbox busy/open from another owner");
 require(in[0]==0x3146504b&&in[1]==1&&in[2]==384&&in[18]==900000,"local KPF1 header");
 uint64_t cap=(uint64_t)in[6]|((uint64_t)in[7]<<32),beg=mono_ns();
 int64_t delta=(int64_t)beg-(int64_t)cap,age=(delta>=0?(delta+999)/1000:-((-delta+999)/1000));
 if((in[10]&1)&&(age< -40000||age>=249000)){printf("{\"kind\":\"over_age_skip\",\"age_us\":%" PRId64 ",\"generation\":%u}\n",age,before);return;}
 require(age>=INT32_MIN&&age<=INT32_MAX,"age representation");in[8]=(uint32_t)(int32_t)age;
 for(unsigned j=0;j<96;j++)prior[j]=rd(&mb,0x400+j*4);
 wr(&mb,0,1);for(unsigned j=0;j<96;j++)wr(&mb,0x100+j*4,in[j]);
 require(rd(&mb,8)==before,"generation changed before SEAL");
 for(unsigned j=0;j<96;j++)require(rd(&mb,0x400+j*4)==prior[j],"non-atomic staging");
 wr(&mb,0,2);uint64_t seal=mono_ns(),deadline=seal+20000000;uint32_t after;
 do{after=rd(&mb,8);if(after!=before)break;}while(mono_ns()<deadline);
 s=safe_status();uint32_t reason=rd(&mb,12);
 if(after==before){printf("{\"kind\":\"rejected\",\"generation\":%u,\"reason\":%u,\"status\":%u}\n",after,reason,s);return;}
 require(after==before+1,"unexpected generation");uint32_t active[96];
 for(unsigned j=0;j<96;j++)active[j]=rd(&mb,0x400+j*4);
 require(rd(&mb,8)==after,"generation unstable during readback");
 unsigned mismatch=0;for(unsigned j=0;j<96;j++)if(active[j]!=in[j])mismatch++;
 printf("{\"kind\":\"commit\",\"generation_before\":%u,\"generation\":%u,\"status\":%u,\"reason\":%u,\"mismatches\":%u,\"atomic_preseal\":true,\"begin_ns\":%" PRIu64 ",\"seal_ns\":%" PRIu64 ",\"verified_ns\":%" PRIu64 ",\"age_us\":%" PRId64 ",\"age_at_seal_us\":%" PRId64 ",\"active_hex\":\"",before,after,s,reason,mismatch,beg,seal,mono_ns(),age,((int64_t)seal-(int64_t)cap)/1000);
 const unsigned char *b=(const unsigned char*)active;for(unsigned j=0;j<384;j++)printf("%02x",b[j]);printf("\"}\n");
 require(!mismatch,"readback mismatch");
}
int main(void){setvbuf(stdout,NULL,_IONBF,0);setvbuf(stderr,NULL,_IONBF,0);logline("LIVE_MAILBOX_ENTRY pid=%ld\n",(long)getpid());require(getuid()==0,"root required");char state[80];require(text("/sys/class/fpga_manager/fpga0/state",state,sizeof state)&&!strcmp(state,"operating"),"manager state");
 diag=map_live("kv260-iic-diag",0x9e000000,0);iic=map_live("kv260-iic-probe",0x9d000000,0);mb=map_live("kv260-pspl-mailbox",0x9f000000,1);snapshot("ready");
 char cmd[4];while(input(cmd,4,1)){if(!memcmp(cmd,"SUBM",4)){uint32_t words[96];input(words,384,0);submit(words);}else if(!memcmp(cmd,"SNAP",4))snapshot("snapshot");else if(!memcmp(cmd,"QUIT",4))break;else require(0,"unknown stream command");}
 snapshot("final");close_window(&mb);close_window(&iic);close_window(&diag);logline("LIVE_MAILBOX_EXIT code=0\n");return 0;}
