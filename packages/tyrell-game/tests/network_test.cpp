#include <tyrell/game/net/codec.hpp>
#include <tyrell/game/net/transport.hpp>
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>

namespace {
namespace net=tyrell::game::net;
void check(bool condition,const std::string& message) {if(!condition) throw std::runtime_error(message);}
void pause() {std::this_thread::sleep_for(std::chrono::milliseconds(1));}
void codec() {
    static_assert(!std::is_constructible<net::ByteReader,std::vector<std::uint8_t>&&>::value,
                  "A borrowing reader must not accept temporary vectors");
    static_assert(!std::is_constructible<net::ByteReader,const std::vector<std::uint8_t>&&>::value,
                  "Const temporary vectors must not bypass the lifetime guard");
    static_assert(std::is_constructible<net::ByteReader,const std::vector<std::uint8_t>&>::value,
                  "Readers must still borrow persistent input vectors");
    net::ByteWriter writer;
    writer.u8(0xab);writer.u16(0x1234);writer.u32(0x89abcdef);writer.f32(1.25f);
    const std::vector<std::uint8_t> golden{0xab,0x12,0x34,0x89,0xab,0xcd,0xef,0x3f,0xa0,0,0};
    check(writer.good() && writer.data()==golden,"portable scalar bytes match independent golden fixture");
    net::ByteReader reader(golden);
    check(reader.u8()==0xab && reader.u16()==0x1234 && reader.u32()==0x89abcdef &&
          reader.f32()==1.25f && reader.finished(),"golden fixture decodes exactly");
    check(reader.u8()==0 && !reader.good(),"over-read latches failure");
    net::ByteReader truncated(golden.data(),2);check(truncated.u32()==0,"truncated u32 returns zero rather than partial bits");
    check(!truncated.good() && !truncated.finished(),"truncated integer is not committed as valid");
    net::ByteReader one_byte(golden.data(),1);
    check(one_byte.u16()==0 && !one_byte.good(),"truncated u16 returns zero rather than partial bits");
    net::ByteReader one_byte_u32(golden.data(),1);
    check(one_byte_u32.u32()==0 && !one_byte_u32.good(),"one-byte u32 truncation cannot leak partial bits");
    net::ByteReader trailing(golden);(void)trailing.u8();
    check(trailing.good() && !trailing.finished() && trailing.remaining()==10,"trailing bytes remain explicit");
    const std::vector<std::uint8_t> nan{0x7f,0xc0,0,0};net::ByteReader nonfinite(nan);
    (void)nonfinite.f32();check(!nonfinite.good(),"nonfinite float rejected at codec boundary");
    net::ByteReader bad_pointer(nullptr,4);check(!bad_pointer.good(),"invalid borrowed input rejected");
    net::ByteWriter capped(2);capped.u16(123);capped.u8(1);
    check(!capped.good() && capped.data().size()==2,"writer cannot exceed explicit packet bound");
    net::ByteWriter invalid_float;invalid_float.f32(std::numeric_limits<float>::infinity());
    check(!invalid_float.good(),"nonfinite float cannot be encoded accidentally");
    check(net::sequence_newer(1,0xfffffffeu) && !net::sequence_newer(0xfffffffeu,1) &&
          !net::sequence_newer(2,2) && !net::sequence_newer(0x80000000u,0),"sequence wrap and half-range ambiguity");
    check(!net::finite_between(std::numeric_limits<float>::quiet_NaN(),-1,1) &&
          net::finite_between(-1,-1,1),"finite range validation includes endpoints");
    bool rejected=false;try {net::Config config;config.channels=0;net::Transport transport(config);}
    catch(const std::invalid_argument&) {rejected=true;}
    check(rejected,"invalid transport bounds rejected before opening a socket");
}

struct Link {net::PeerId client=0,server=0;};
Link connect(net::Transport& host,net::Transport& client,std::uint32_t data=0) {
    Link link;link.client=client.connect("127.0.0.1",host.port(),data);
    check(link.client!=0,"outgoing peer allocated: "+client.error());
    bool local=false,remote=false;
    for(int i=0;i<1000 && !(local&&remote);++i) {
        net::Event event;
        while(host.poll(event)) if(event.type==net::EventType::Connected) {
            link.server=event.peer;remote=true;check(event.data==data,"connect metadata reaches host");
        }
        while(client.poll(event)) if(event.type==net::EventType::Connected) {
            check(event.peer==link.client,"client peer ID is stable through connect");local=true;
        }
        host.flush();client.flush();pause();
    }
    check(local&&remote,"real UDP connection completes");return link;
}
net::Event receive(net::Transport& receiver,net::Transport& sender) {
    for(int i=0;i<1000;++i) {
        net::Event event;
        if(receiver.poll(event) && event.type==net::EventType::Received) return event;
        while(sender.poll(event)) {}
        receiver.flush();sender.flush();pause();
    }
    throw std::runtime_error("real UDP packet did not arrive");
}
void transport() {
    net::Transport host,client,second;
    check(!host.active() && host.port()==0,"new transport owns no socket");
    check(host.host(0) && host.port()!=0,"OS-assigned listening port is discoverable: "+host.error());
    const auto first=connect(host,client,42);
    const auto other=connect(host,second,7);
    check(first.server!=other.server,"simultaneous peers have distinct opaque IDs");
    const std::vector<std::uint8_t> control{1,0,255,3,0};
    check(client.send(first.client,net::kControlChannel,control,true),"reliable control queued");
    client.flush();auto event=receive(host,client);
    check(event.peer==first.server && event.channel==net::kControlChannel && event.reliable && event.payload==control,
          "reliable event preserves identity/channel/owned binary bytes");
    const auto saved=event;
    check(host.send(first.server,net::kSnapshotChannel,{9,8,7},false),"unreliable snapshot queued");
    host.flush();event=receive(client,host);
    check(event.peer==first.client && event.channel==net::kSnapshotChannel && !event.reliable &&
          event.payload==std::vector<std::uint8_t>({9,8,7}),"unreliable snapshot channel is separate");
    check(client.send(first.client,net::kControlChannel,{},true),"empty application message queued");client.flush();
    event=receive(host,client);check(event.payload.empty() && event.reliable,"empty message has valid owned storage semantics");
    check(!client.send(first.client,3,{1}) && !client.send(first.client,0,std::vector<std::uint8_t>(1201)) &&
          !client.send(0,0,{1}),"channel/size/peer bounds fail without queueing");
    host.disconnect(first.server,73);host.flush();
    bool disconnected=false;
    for(int i=0;i<500&&!disconnected;++i) {
        while(client.poll(event)) if(event.type==net::EventType::Disconnected) {
            disconnected=true;check(event.peer==first.client && event.data==73,"disconnect identifies the original peer and reason");
        }
        pause();
    }
    check(disconnected && !host.send(first.server,0,{1}),"disconnect invalidates stale peer ID");
    const auto reconnected=connect(host,client,43);
    check(reconnected.server!=first.server && reconnected.client!=first.client,"reconnect never reuses a stale ID");
    check(host.send(other.server,net::kInputChannel,{22}),"remaining peer remains addressable");host.flush();
    event=receive(second,host);check(event.payload==std::vector<std::uint8_t>({22}),"peer isolation survives another peer reconnect");
    host.close();client.close();second.close();
    check(saved.payload==control,"received bytes remain owned after endpoint destruction");
    check(!host.active() && !host.send(reconnected.server,0,{1}),"closed endpoint cannot send");
    check(host.host(0),"closed transport can reopen");
    const auto reopened=connect(host,client,44);
    check(reopened.server!=reconnected.server,"close/reopen also preserves generation safety");
    check(!second.connect("",host.port()) && !second.active() && !second.error().empty(),"resolution failure reports usable error without leaked socket");
}
void backpressure() {
    net::Config config;config.max_queued_packets=2;config.max_queued_bytes=2400;
    net::Transport host,client(config);check(host.host(0),"queue test host");
    auto link=connect(host,client);
    const std::vector<std::uint8_t> full(1200,1);
    check(client.send(link.client,0,full,true) && client.send(link.client,0,full,true),"bounded queue accepts its budget");
    check(!client.send(link.client,0,{1},true) && client.error()=="Outgoing packet queue is full","queue backpressure is explicit");
    client.close();
    // Closing destroys queued ENet packets and releases their budget callbacks.
    link=connect(host,client);
    check(client.send(link.client,0,full,true),"queue budget is released on close/reconnect");
    client.flush();(void)receive(host,client);
    // Service ACKs so a reliable packet's budget can be released normally.
    for(int i=0;i<20;++i) {net::Event event;while(client.poll(event)){}while(host.poll(event)){}client.flush();host.flush();pause();}
    check(client.send(link.client,0,full,true),"acknowledged packets release outgoing budget");
}
}
int main() {
    try {codec();transport();backpressure();std::cout<<"framework network: codecs, real UDP, channel separation, peer lifetime, and bounded queues passed\n";return 0;}
    catch(const std::exception& error) {std::cerr<<"framework network test: "<<error.what()<<'\n';return 1;}
}
