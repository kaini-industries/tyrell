#define _GNU_SOURCE
#include <dlfcn.h>
#include <gst/gst.h>
#include <gst/allocators/gstdmabuf.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Temporary qualification interposer, never part of the app/package.
 * Only kmssink's own activation of its KMS pool can consume this budget.
 * This is a controlled injected failure, not a simulated measurement of CMA.
 */
static pthread_once_t once = PTHREAD_ONCE_INIT;
static pthread_mutex_t pool_marks = PTHREAD_MUTEX_INITIALIZER;
static atomic_uint distinct_matching_pools;
static atomic_bool reported_dma_rejection;
static unsigned failure_limit;
static gboolean reject_kms_dmabuf;
static GQuark injected_pool;
static gboolean (*next_set_active)(GstBufferPool *, gboolean);
static gboolean (*next_bus_post)(GstBus *, GstMessage *);
static gboolean (*next_is_dmabuf)(GstMemory *);

static void initialize(void) {
    void *active = dlsym(RTLD_NEXT, "gst_buffer_pool_set_active");
    void *post = dlsym(RTLD_NEXT, "gst_bus_post");
    void *dmabuf = dlsym(RTLD_NEXT, "gst_is_dmabuf_memory");
    _Static_assert(sizeof(active) == sizeof(next_set_active), "POSIX function pointer ABI required");
    _Static_assert(sizeof(post) == sizeof(next_bus_post), "POSIX function pointer ABI required");
    _Static_assert(sizeof(dmabuf) == sizeof(next_is_dmabuf), "POSIX function pointer ABI required");
    memcpy(&next_set_active, &active, sizeof(next_set_active));
    memcpy(&next_bus_post, &post, sizeof(next_bus_post));
    memcpy(&next_is_dmabuf, &dmabuf, sizeof(next_is_dmabuf));
    if(!next_set_active || !next_bus_post || !next_is_dmabuf) {
        fprintf(stderr, "TYRELL_KMS_FAULT unresolved original GStreamer symbols\n");
        _exit(127);
    }
    const char *value = getenv("TYRELL_KMS_FAIL_ACTIVATIONS");
    if(value && strcmp(value, "1") == 0) failure_limit = 1;
    else if(value && strcmp(value, "2") == 0) failure_limit = 2;
    value = getenv("TYRELL_KMS_REJECT_DMABUF");
    reject_kms_dmabuf = value && strcmp(value, "1") == 0;
    injected_pool = g_quark_from_static_string("tyrell-kms-fault-pool-already-considered");
    fprintf(stderr, "TYRELL_KMS_FAULT initialized pid=%ld limit=%u reject_kms_dmabuf=%d\n",
            (long)getpid(), failure_limit, reject_kms_dmabuf);
}

static void ready(void) {
    if(pthread_once(&once, initialize) != 0) {
        fprintf(stderr, "TYRELL_KMS_FAULT initialization failed\n");
        _exit(127);
    }
}

static const char *basename_of(const char *path) {
    if(!path) return "";
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

__attribute__((noinline))
gboolean gst_buffer_pool_set_active(GstBufferPool *pool, gboolean active) {
    ready();
    if(failure_limit && active && pool && strcmp(G_OBJECT_TYPE_NAME(pool), "GstKMSBufferPool") == 0) {
        void *caller = __builtin_extract_return_addr(__builtin_return_address(0));
        Dl_info location = {0};
        if(dladdr(caller, &location) && strcmp(basename_of(location.dli_fname), "libgstkms.so") == 0) {
            unsigned number = 0;
            pthread_mutex_lock(&pool_marks);
            if(!g_object_get_qdata(G_OBJECT(pool), injected_pool)) {
                g_object_set_qdata(G_OBJECT(pool), injected_pool, GUINT_TO_POINTER(1));
                number = atomic_fetch_add_explicit(&distinct_matching_pools, 1, memory_order_relaxed) + 1;
            }
            pthread_mutex_unlock(&pool_marks);
            if(number && number <= failure_limit) {
                const uintptr_t offset = (uintptr_t)caller - (uintptr_t)location.dli_fbase;
                fprintf(stderr,
                        "TYRELL_KMS_FAULT injected=%u limit=%u pid=%ld pool=GstKMSBufferPool distinct_pool_number=%u pool_addr=%p caller=libgstkms.so offset=0x%" PRIxPTR "\n",
                        number, failure_limit, (long)getpid(), number, (void *)pool, offset);
                return FALSE;
            }
        }
    }
    return next_set_active(pool, active);
}

__attribute__((noinline))
gboolean gst_is_dmabuf_memory(GstMemory *memory) {
    ready();
    const gboolean actual = next_is_dmabuf(memory);
    if(actual && reject_kms_dmabuf) {
        void *caller = __builtin_extract_return_addr(__builtin_return_address(0));
        Dl_info location = {0};
        if(dladdr(caller, &location) && strcmp(basename_of(location.dli_fname), "libgstkms.so") == 0) {
            if(!atomic_exchange_explicit(&reported_dma_rejection, 1, memory_order_relaxed))
                fprintf(stderr, "TYRELL_KMS_FAULT reject_dmabuf=1 actual_dmabuf=1 pid=%ld caller=libgstkms.so\n", (long)getpid());
            return FALSE;
        }
    }
    return actual;
}

gboolean gst_bus_post(GstBus *bus, GstMessage *message) {
    ready();
    if(message && GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
        GError *error = NULL;
        gchar *debug = NULL;
        gst_message_parse_error(message, &error, &debug);
        GstObject *source = GST_MESSAGE_SRC(message);
        const char *domain = error ? g_quark_to_string(error->domain) : NULL;
        fprintf(stderr, "TYRELL_KMS_FAULT actual_bus_error source=%s type=%s domain=%s code=%d message=%s\n",
                source ? GST_OBJECT_NAME(source) : "(null)",
                source ? G_OBJECT_TYPE_NAME(source) : "(null)",
                domain ? domain : "(null)", error ? error->code : -1,
                error && error->message ? error->message : "(null)");
        if(error) g_error_free(error);
        g_free(debug);
    }
    /* Preserve the actual message and its ownership; no synthesized bus errors. */
    return next_bus_post(bus, message);
}
