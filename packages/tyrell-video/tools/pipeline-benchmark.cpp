// Uninstalled profiling harness: compare decode, conversion and presentation
// paths without conflating buffer arrivals with physical display refresh.
#include "json_string.h"

#include <gst/gst.h>
#include <sys/resource.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <iostream>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
using tyrell::video::json_string;

namespace {
struct Stage {
    std::string name, factory, pad, caps, memory;
    std::atomic<std::uint64_t> buffers{0}, bytes{0};
    std::atomic<guint64> first_pts{GST_CLOCK_TIME_NONE}, last_pts{GST_CLOCK_TIME_NONE};
    std::mutex metadata;
};

struct Run {
    std::mutex mutex;
    std::set<GstPad*> attached;
    std::vector<std::unique_ptr<Stage>> stages;

    static void buffer(Stage& stage, GstBuffer* value) {
        if(!value) return;
        const auto previous = stage.buffers.fetch_add(1);
        stage.bytes.fetch_add(gst_buffer_get_size(value));
        const auto pts = GST_BUFFER_PTS(value);
        if(GST_CLOCK_TIME_IS_VALID(pts)) {
            guint64 empty = GST_CLOCK_TIME_NONE;
            stage.first_pts.compare_exchange_strong(empty, pts);
            stage.last_pts = pts;
        }
        if(previous == 0) {
            std::set<std::string> types;
            for(guint i = 0; i < gst_buffer_n_memory(value); ++i) {
                GstMemory* memory = gst_buffer_peek_memory(value, i);
                types.emplace(memory && memory->allocator && memory->allocator->mem_type
                              ? memory->allocator->mem_type : "unknown");
            }
            std::lock_guard<std::mutex> lock(stage.metadata);
            for(const auto& type : types) {
                if(!stage.memory.empty()) stage.memory += ',';
                stage.memory += type;
            }
        }
    }

    static GstPadProbeReturn probe(GstPad*, GstPadProbeInfo* info, gpointer data) {
        auto& stage = *static_cast<Stage*>(data);
        if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)
            buffer(stage, GST_PAD_PROBE_INFO_BUFFER(info));
        else if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
            auto* list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
            for(guint i = 0; list && i < gst_buffer_list_length(list); ++i)
                buffer(stage, gst_buffer_list_get(list, i));
        } else if(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM) {
            GstEvent* event = GST_PAD_PROBE_INFO_EVENT(info);
            if(event && GST_EVENT_TYPE(event) == GST_EVENT_CAPS) {
                GstCaps* caps = nullptr;
                gst_event_parse_caps(event, &caps);
                gchar* text = gst_caps_to_string(caps);
                std::lock_guard<std::mutex> lock(stage.metadata);
                stage.caps = text ? text : "";
                g_free(text);
            }
        }
        return GST_PAD_PROBE_OK;
    }

    void add(GstElement* element) {
        GstElementFactory* f = gst_element_get_factory(element);
        if(!f) return;
        const char* factory = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(f));
        const char* klass = gst_element_factory_get_metadata(f, GST_ELEMENT_METADATA_KLASS);
        const bool decoder = klass && std::strstr(klass, "Decoder") && std::strstr(klass, "Video");
        const bool converter = klass && std::strstr(klass, "Video") &&
                               (std::strstr(klass, "Converter") || std::strstr(klass, "Scale") || std::strstr(klass, "Overlay"));
        const bool sink = klass && std::strstr(klass, "Sink");
        if(!(decoder || converter || sink || std::strcmp(factory, "identity") == 0)) return;
        GstPad* pad = gst_element_get_static_pad(element, sink ? "sink" : "src");
        if(!pad) return;
        std::lock_guard<std::mutex> lock(mutex);
        if(attached.insert(pad).second) {
            auto stage = std::make_unique<Stage>();
            stage->name = GST_ELEMENT_NAME(element);
            stage->factory = factory;
            stage->pad = sink ? "sink" : "src";
            gst_pad_add_probe(pad, static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER |
                              GST_PAD_PROBE_TYPE_BUFFER_LIST | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM), probe, stage.get(), nullptr);
            stages.push_back(std::move(stage));
        }
        gst_object_unref(pad);
    }

    static void added(GstBin*, GstBin*, GstElement* element, gpointer data) {
        static_cast<Run*>(data)->add(element);
    }

    void initial(GstElement* pipeline) {
        g_signal_connect(pipeline, "deep-element-added", G_CALLBACK(added), this);
        GstIterator* iterator = gst_bin_iterate_recurse(GST_BIN(pipeline));
        GValue value = G_VALUE_INIT;
        for(;;) {
            const auto result = gst_iterator_next(iterator, &value);
            if(result == GST_ITERATOR_RESYNC) { gst_iterator_resync(iterator); continue; }
            if(result != GST_ITERATOR_OK) break;
            add(GST_ELEMENT(g_value_get_object(&value)));
            g_value_reset(&value);
        }
        if(G_IS_VALUE(&value)) g_value_unset(&value);
        gst_iterator_free(iterator);
    }
};

void describe(const std::vector<std::string>& factories) {
    std::cout << "{\"factories\":[";
    bool comma = false;
    for(const auto& name : factories) {
        if(comma) std::cout << ",";
        comma = true;
        GstElement* element = gst_element_factory_make(name.c_str(), nullptr);
        std::cout << "{\"name\":" << json_string(name) << ",\"available\":" << (element ? "true" : "false");
        if(element) {
            GstElementFactory* factory = gst_element_get_factory(element);
            std::cout << ",\"pads\":[";
            bool pad_comma = false;
            for(const GList* item = gst_element_factory_get_static_pad_templates(factory); item; item = item->next) {
                auto* pad = static_cast<GstStaticPadTemplate*>(item->data);
                GstCaps* caps = gst_static_pad_template_get_caps(pad);
                gchar* text = gst_caps_to_string(caps);
                if(pad_comma) std::cout << ",";
                pad_comma = true;
                std::cout << "{\"name\":" << json_string(pad->name_template) << ",\"caps\":" << json_string(text ? text : "") << '}';
                g_free(text); gst_caps_unref(caps);
            }
            std::cout << "],\"properties\":[";
            guint count = 0;
            GParamSpec** properties = g_object_class_list_properties(G_OBJECT_GET_CLASS(element), &count);
            for(guint i = 0; i < count; ++i) {
                if(i) std::cout << ",";
                gchar* value = g_strdup_value_contents(g_param_spec_get_default_value(properties[i]));
                std::cout << "{\"name\":" << json_string(properties[i]->name) << ",\"type\":" << json_string(g_type_name(properties[i]->value_type)) << ",\"default\":" << json_string(value ? value : "") << '}';
                g_free(value);
            }
            g_free(properties);
            std::cout << ']';
            gst_object_unref(element);
        }
        std::cout << '}';
    }
    std::cout << "]}\n";
}

double cpu(const rusage& r) {
    return r.ru_utime.tv_sec + r.ru_stime.tv_sec + (r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1000000.0;
}
} // namespace

int main(int argc, char** argv) {
    try {
        double seconds = 30;
        std::string description;
        std::vector<std::string> descriptions;
        for(int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if(arg == "--seconds" && i + 1 < argc) seconds = std::stod(argv[++i]);
            else if(arg == "--describe" && i + 1 < argc) descriptions.push_back(argv[++i]);
            else if(arg == "--help") {
                std::cout << "pipeline-benchmark [--seconds 30] 'GSTREAMER PIPELINE'\n"
                             "Uninstalled developer tool. Counts buffers and memory at decoder/converter/sink pads.\n"
                             "Use sync=false for maximum-throughput tests, sync=true for clocked presentation.\n";
                return 0;
            } else if(description.empty()) description = arg;
            else throw std::runtime_error("Pass one quoted GStreamer pipeline string");
        }
        if(!descriptions.empty()) { gst_init(nullptr, nullptr); describe(descriptions); return 0; }
        if(description.empty() || !std::isfinite(seconds) || seconds <= 0 || seconds > 600)
            throw std::runtime_error("Invalid pipeline or runtime; use --help");
        gst_init(nullptr, nullptr);
        GError* error = nullptr;
        GstElement* pipeline = gst_parse_launch(description.c_str(), &error);
        if(!pipeline || error || !GST_IS_BIN(pipeline)) {
            const std::string message = error ? error->message : "Pipeline must contain a bin";
            if(error) g_error_free(error);
            if(pipeline) gst_object_unref(pipeline);
            throw std::runtime_error(message);
        }
        Run run;
        run.initial(pipeline);
        GstBus* bus = gst_element_get_bus(pipeline);
        rusage before{}; getrusage(RUSAGE_SELF, &before);
        const auto start = Clock::now();
        auto playing = Clock::time_point{};
        bool eos = false;
        std::string failure;
        if(gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
            failure = "Pipeline could not start";
        while(failure.empty() && !eos && std::chrono::duration<double>(Clock::now() - start).count() < seconds) {
            GstMessage* message = gst_bus_timed_pop(bus, 100 * GST_MSECOND);
            if(!message) continue;
            if(GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
                GError* detail = nullptr; gchar* debug = nullptr;
                gst_message_parse_error(message, &detail, &debug);
                failure = detail ? detail->message : "Unknown pipeline error";
                if(detail) g_error_free(detail);
                g_free(debug);
            } else if(GST_MESSAGE_TYPE(message) == GST_MESSAGE_EOS) eos = true;
            else if(GST_MESSAGE_TYPE(message) == GST_MESSAGE_STATE_CHANGED && GST_MESSAGE_SRC(message) == GST_OBJECT(pipeline)) {
                GstState old_state, new_state, pending;
                gst_message_parse_state_changed(message, &old_state, &new_state, &pending);
                if(new_state == GST_STATE_PLAYING && playing == Clock::time_point{}) {
                    playing = Clock::now();
                    GST_DEBUG_BIN_TO_DOT_FILE_WITH_TS(GST_BIN(pipeline), GST_DEBUG_GRAPH_SHOW_ALL, "tyrell-benchmark-playing");
                }
            }
            gst_message_unref(message);
        }
        gint64 position = 0;
        gst_element_query_position(pipeline, GST_FORMAT_TIME, &position);
        const auto end = Clock::now();
        const double elapsed = std::chrono::duration<double>(end - start).count();
        const double active = playing == Clock::time_point{} ? 0 : std::chrono::duration<double>(end - playing).count();
        rusage after{}; getrusage(RUSAGE_SELF, &after);
        // Stop and join every streaming task before reading stage metadata.
        gst_element_set_state(pipeline, GST_STATE_NULL);
        gst_object_unref(bus);
        gst_object_unref(pipeline);
        std::cout << "{\"ok\":" << (failure.empty() ? "true" : "false")
                  << ",\"error\":" << json_string(failure) << ",\"eos\":" << (eos ? "true" : "false")
                  << ",\"elapsed_seconds\":" << elapsed << ",\"playing_seconds\":" << active
                  << ",\"position_seconds\":" << position / static_cast<double>(GST_SECOND)
                  << ",\"cpu_seconds\":" << cpu(after) - cpu(before) << ",\"stages\":[";
        bool comma = false;
        for(const auto& stage : run.stages) {
            if(comma) std::cout << ',';
            comma = true;
            std::cout << "{\"name\":" << json_string(stage->name) << ",\"factory\":" << json_string(stage->factory)
                      << ",\"pad\":" << json_string(stage->pad) << ",\"buffers\":" << stage->buffers.load()
                      << ",\"bytes\":" << stage->bytes.load() << ",\"caps\":" << json_string(stage->caps)
                      << ",\"memory\":" << json_string(stage->memory)
                      << ",\"first_pts_seconds\":" << (GST_CLOCK_TIME_IS_VALID(stage->first_pts.load()) ? stage->first_pts.load() / static_cast<double>(GST_SECOND) : -1)
                      << ",\"last_pts_seconds\":" << (GST_CLOCK_TIME_IS_VALID(stage->last_pts.load()) ? stage->last_pts.load() / static_cast<double>(GST_SECOND) : -1) << '}';
        }
        std::cout << "],\"measurement\":\"Pad buffer delivery and throughput; not physical scanout or audiovisual synchronization\"}\n";
        return failure.empty() ? 0 : 1;
    } catch(const std::exception& error) {
        std::cerr << "pipeline-benchmark: " << error.what() << '\n';
        return 2;
    }
}
