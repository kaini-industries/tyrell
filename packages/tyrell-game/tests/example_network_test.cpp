#include "beacon_session.hpp"
#include <tyrell/game/net/codec.hpp>
#include <tyrell/game/net/transport.hpp>
#include <chrono>
#include <cmath>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>

namespace {
namespace net=tyrell::game::net;
void require(bool okay,const char* message) {if(!okay) throw std::runtime_error(message);}
void wait_for(const std::function<bool()>& action,const char* message) {
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(4);
    do {
        if(action()) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    } while(std::chrono::steady_clock::now()<deadline);
    throw std::runtime_error(message);
}
net::ByteWriter message(std::uint8_t type,std::uint16_t version=1) {
    net::ByteWriter out;out.u32(0x54594243);out.u16(version);out.u8(type);out.u8(0);return out;
}
int actors(const beacon::State& state) {int result=0;for(const auto& a:state.actors) result+=a.active;return result;}
void offline() {
    beacon::Session game;
    for(int i=0;i<60;++i) game.update(1.0/60.0,{1,0,true});
    require(std::abs(game.state().actors[0].x-(-2))<.01f,"Offline fixed-step movement failed");
    require(game.state().actors[0].pulse==1,"Pulse input failed");
    for(int i=0;i<60;++i) game.update(1.0/60.0,{});
    require(game.state().actors[0].pulse==0,"Pulse decay failed");
}
void loopback() {
    beacon::Session host,first,second;
    require(host.host(0),"Cannot bind example UDP host");
    require(first.join("127.0.0.1",host.port()),"Cannot create first client");
    require(second.join("127.0.0.1",host.port()),"Cannot create second client");
    wait_for([&] {
        host.update(.01,{});first.update(.01,{});second.update(.01,{});
        return first.connected()&&second.connected()&&actors(first.state())==3;
    },"Independent example handshake failed");
    require(first.local_player()!=second.local_player(),"Clients share a player slot");
    const int slot=first.local_player();const float before=host.state().actors[slot].x;
    wait_for([&] {
        host.update(.01,{});first.update(.01,{-1,.25f,true});second.update(.01,{});
        return host.state().actors[slot].x<before-.5f&&first.state().actors[slot].pulse>.5f;
    },"Authoritative input/snapshot replication failed");
    require(std::isfinite(second.state().actors[slot].x),"Nonfinite replicated state");
    first.offline();
    wait_for([&] {host.update(.01,{});second.update(.01,{});return actors(host.state())==2;},"Disconnect did not remove the player");
}
void reject_bad_clients() {
    for(int kind=0;kind<3;++kind) {
        beacon::Session host;net::Transport client;
        require(host.host(0),"Cannot bind validation host");
        const auto peer=client.connect("127.0.0.1",host.port());require(peer!=0,"Raw connect failed");
        bool sent=false,welcomed=false,dropped=false;
        wait_for([&] {
            host.update(.01,{});net::Event event;
            while(client.poll(event)) {
                if(event.type==net::EventType::Connected) {
                    auto out=message(1,kind==0?2:1);
                    if(kind==1) out.u8(99); // An otherwise valid hello with a trailing byte.
                    client.send(peer,net::kControlChannel,out.data(),true);sent=true;
                } else if(event.type==net::EventType::Received&&kind==2&&!welcomed) {
                    net::ByteReader in(event.payload);
                    if(in.u32()==0x54594243&&in.u16()==1&&in.u8()==2) {
                        welcomed=true;
                        auto out=message(3);out.u32(1);out.u32(0x7fc00000);out.f32(0);out.u8(0);
                        client.send(peer,net::kInputChannel,out.data()); // NaN encoded deliberately as raw bits.
                    }
                } else if(event.type==net::EventType::Disconnected) dropped=true;
            }
            client.flush();return sent&&dropped;
        },"Malformed/versioned client was not rejected");
        require(actors(host.state())==1,"Rejected client left an active actor");
    }
}
void reject_bad_snapshot() {
    net::Transport server;beacon::Session client;
    require(server.host(0),"Cannot bind invalid-snapshot server");
    require(client.join("127.0.0.1",server.port()),"Invalid-snapshot client connect failed");
    bool sent=false;
    wait_for([&] {
        client.update(.01,{});net::Event event;
        while(server.poll(event)) if(event.type==net::EventType::Received&&!sent) {
            auto welcome=message(2);welcome.u8(1);server.send(event.peer,net::kControlChannel,welcome.data(),true);
            auto state=message(4);state.u32(1);
            for(int i=0;i<4;++i) {state.u8(i==1?1:0);state.u32(i==1?0x7fc00000:0);state.f32(0);state.f32(0);}
            require(state.data().size()==64,"Example snapshot schema size changed");
            server.send(event.peer,net::kSnapshotChannel,state.data());server.flush();sent=true;
        }
        return sent&&client.status()=="Invalid snapshot";
    },"Malformed snapshot was accepted");
    require(!client.connected()&&actors(client.state())==0,"Invalid server state remained playable");
}
void stale_state_after_frame_gap() {
    net::Transport server;beacon::Session client;
    require(server.host(0),"Cannot bind snapshot-timeout server");
    require(client.join("127.0.0.1",server.port()),"Snapshot-timeout client connect failed");
    bool sent=false;
    wait_for([&] {
        client.update(.01,{});net::Event event;
        while(server.poll(event)) if(event.type==net::EventType::Received&&!sent) {
            auto welcome=message(2);welcome.u8(1);server.send(event.peer,net::kControlChannel,welcome.data(),true);
            auto state=message(4);state.u32(1);
            for(int i=0;i<4;++i) {state.u8(i==1?1:0);state.f32(0);state.f32(0);state.f32(0);}
            server.send(event.peer,net::kSnapshotChannel,state.data());server.flush();sent=true;
        }
        return sent&&client.connected();
    },"Snapshot-timeout setup failed");
    client.update(3.1,{});
    require(!client.connected()&&client.status()=="Snapshot timeout",
            "Simulation catch-up cap incorrectly extended snapshot liveness");
}
void snapshots_do_not_replace_welcome() {
    net::Transport server;beacon::Session client;
    require(server.host(0),"Cannot bind incomplete-handshake server");
    require(client.join("127.0.0.1",server.port()),"Incomplete-handshake client connect failed");
    net::PeerId remote=0;
    auto send_state=[&](std::uint32_t tick) {
        auto state=message(4);state.u32(tick);
        for(int i=0;i<4;++i) {state.u8(i==1?1:0);state.f32(0);state.f32(0);state.f32(0);}
        server.send(remote,net::kSnapshotChannel,state.data());server.flush();
    };
    wait_for([&] {
        client.update(.01,{});net::Event event;
        while(server.poll(event)) if(event.type==net::EventType::Received&&!remote) {
            remote=event.peer;send_state(1);
        }
        return client.state().tick==1;
    },"Incomplete-handshake setup failed");
    require(client.local_player()==-1&&!client.connected(),"Snapshot assigned an unauthorized local slot");
    client.update(2.5,{});send_state(2);
    wait_for([&] {client.update(.01,{});return client.state().tick==2;},"Fresh snapshot setup failed");
    client.update(2.6,{});
    require(client.status()=="Handshake timeout"&&!client.connected(),
            "Fresh snapshots kept an incomplete handshake alive indefinitely");
}
}
int main() {
    try {offline();loopback();reject_bad_clients();reject_bad_snapshot();stale_state_after_frame_gap();snapshots_do_not_replace_welcome();std::cout<<"Independent beacon example tests passed\n";return 0;}
    catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
