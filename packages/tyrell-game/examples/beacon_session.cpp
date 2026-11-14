#include "beacon_session.hpp"
#include <tyrell/game/net/codec.hpp>
#include <algorithm>
#include <cmath>

namespace beacon {
namespace {
namespace net=tyrell::game::net;
constexpr std::uint32_t magic=0x54594243; // TYBC: separate from Sandline's wire protocol.
constexpr std::uint16_t version=1;
enum class Message:std::uint8_t {Hello=1,Welcome=2,Input=3,Snapshot=4};
net::ByteWriter packet(Message type) {
    net::ByteWriter out;
    out.u32(magic);out.u16(version);out.u8(static_cast<std::uint8_t>(type));out.u8(0);
    return out;
}
bool header(net::ByteReader& in,Message& type) {
    if(in.size()<8 || in.u32()!=magic || in.u16()!=version) return false;
    type=static_cast<Message>(in.u8());
    return in.u8()==0 && in.good();
}
Input sanitize(Input input) {
    input.x=std::isfinite(input.x)?std::clamp(input.x,-1.0f,1.0f):0;
    input.z=std::isfinite(input.z)?std::clamp(input.z,-1.0f,1.0f):0;
    return input;
}
Actor spawn(int index) {
    constexpr float x[]={-5,5,5,-5},z[]={-3,3,-3,3};
    return {true,x[index],z[index],0};
}
net::ByteWriter snapshot(const State& state) {
    auto out=packet(Message::Snapshot);out.u32(state.tick);
    for(const auto& actor:state.actors) {
        out.u8(actor.active?1:0);out.f32(actor.x);out.f32(actor.z);out.f32(actor.pulse);
    }
    return out;
}
bool decode_snapshot(net::ByteReader& in,State& state) {
    if(in.size()!=64) return false;
    State next;next.tick=in.u32();
    for(auto& actor:next.actors) {
        const auto active=in.u8();if(active>1) return false;
        actor={active!=0,in.f32(),in.f32(),in.f32()};
        if(!net::finite_between(actor.x,-7,7)||!net::finite_between(actor.z,-5,5)||
           !net::finite_between(actor.pulse,0,1)) return false;
    }
    if(!in.finished()) return false;
    state=next;return true;
}
} // namespace

Session::Session():simulation_(1.0/60.0,8) {state_.actors[0]=spawn(0);}
void Session::clear() {
    transport_.close();simulation_.reset();state_={};peers_.fill(0);ready_.fill(false);
    has_input_.fill(false);inputs_.fill({});sequences_.fill(0);joined_.fill(0);last_input_.fill(0);
    server_=0;local_=-1;next_input_=0;clock_=last_state_=snapshot_clock_=input_clock_=0;
    received_state_=false;
}
void Session::offline() {
    clear();mode_=Mode::Offline;local_=0;state_.actors[0]=spawn(0);status_="Offline";
}
bool Session::host(std::uint16_t port) {
    clear();mode_=Mode::Host;
    if(!transport_.host(port)) {status_=transport_.error();return false;}
    local_=0;state_.actors[0]=spawn(0);status_="Hosting UDP "+std::to_string(transport_.port());return true;
}
bool Session::join(const std::string& hostname,std::uint16_t port) {
    clear();mode_=Mode::Client;server_=transport_.connect(hostname,port);
    if(!server_) {status_=transport_.error();return false;}
    status_="Connecting";return true;
}
bool Session::connected() const {return mode_==Mode::Host?transport_.active():mode_==Mode::Client&&local_>=0&&received_state_;}
bool Session::is_host() const {return mode_==Mode::Host&&transport_.active();}
void Session::disconnect(const std::string& reason) {
    if(server_) transport_.disconnect(server_);
    server_=0;local_=-1;state_={};received_state_=false;status_=reason;
}
void Session::publish() {
    const auto out=snapshot(state_);
    for(int i=1;i<kPlayers;++i) if(peers_[i]&&ready_[i])
        transport_.send(peers_[i],net::kSnapshotChannel,out.data());
}
void Session::receive_host(const net::Event& event) {
    int slot=-1;for(int i=1;i<kPlayers;++i) if(peers_[i]==event.peer) slot=i;
    auto reject=[&]() {
        transport_.disconnect(event.peer,1);
        if(slot>0) {peers_[slot]=0;ready_[slot]=false;inputs_[slot]={};state_.actors[slot]={};}
    };
    if(slot<1) {reject();return;}
    net::ByteReader in(event.payload);Message type{};
    if(!header(in,type)) {reject();return;}
    if(type==Message::Hello&&event.channel==net::kControlChannel&&!ready_[slot]&&in.finished()) {
        ready_[slot]=true;state_.actors[slot]=spawn(slot);last_input_[slot]=clock_;
        auto out=packet(Message::Welcome);out.u8(static_cast<std::uint8_t>(slot));
        transport_.send(event.peer,net::kControlChannel,out.data(),true);publish();return;
    }
    if(type==Message::Input&&event.channel==net::kInputChannel&&ready_[slot]) {
        const auto sequence=in.u32();Input input{in.f32(),in.f32(),false};
        const auto pulse=in.u8();input.pulse=pulse!=0;
        if(!in.finished()||pulse>1||!net::finite_between(input.x,-1,1)||!net::finite_between(input.z,-1,1)) {reject();return;}
        if(!has_input_[slot]||net::sequence_newer(sequence,sequences_[slot])) {
            inputs_[slot]=input;sequences_[slot]=sequence;has_input_[slot]=true;last_input_[slot]=clock_;
        }
        return;
    }
    reject();
}
void Session::receive_client(const net::Event& event) {
    if(event.peer!=server_) return;
    net::ByteReader in(event.payload);Message type{};
    if(!header(in,type)) {disconnect("Invalid server packet");return;}
    if(type==Message::Welcome&&event.channel==net::kControlChannel&&local_<0) {
        const int slot=in.u8();
        if(!in.finished()||slot<1||slot>=kPlayers) {disconnect("Invalid player slot");return;}
        local_=slot;status_="Joined as beacon "+std::to_string(slot+1);return;
    }
    if(type==Message::Snapshot&&event.channel==net::kSnapshotChannel) {
        State next;
        if(!decode_snapshot(in,next)) {disconnect("Invalid snapshot");return;}
        if(!received_state_||net::sequence_newer(next.tick,state_.tick)) {
            state_=next;received_state_=true;last_state_=clock_;
        }
        return;
    }
    disconnect("Unexpected server packet");
}
void Session::update(double elapsed,Input input) {
    if(!std::isfinite(elapsed)||elapsed<0) elapsed=0;
    // Liveness follows wall time even when simulation catch-up is capped.
    // A resumed three-second stall must not present a stale world as fresh.
    const double network_elapsed=std::min(elapsed,60.0);clock_+=network_elapsed;
    net::Event event;
    for(int count=0;count<128&&transport_.poll(event);++count) {
        if(event.type==net::EventType::Connected) {
            if(mode_==Mode::Host) {
                int free=-1;for(int i=1;i<kPlayers;++i) if(!peers_[i]) {free=i;break;}
                if(free<0) transport_.disconnect(event.peer,2);
                else {peers_[free]=event.peer;joined_[free]=clock_;ready_[free]=false;has_input_[free]=false;inputs_[free]={};}
            } else if(mode_==Mode::Client&&event.peer==server_) {
                transport_.send(server_,net::kControlChannel,packet(Message::Hello).data(),true);
            }
        } else if(event.type==net::EventType::Disconnected) {
            if(mode_==Mode::Host) {
                for(int i=1;i<kPlayers;++i) if(peers_[i]==event.peer) {
                    peers_[i]=0;ready_[i]=false;inputs_[i]={};state_.actors[i]={};
                }
            } else if(mode_==Mode::Client&&event.peer==server_) disconnect("Disconnected from host");
        } else if(mode_==Mode::Host) receive_host(event);
        else if(mode_==Mode::Client) receive_client(event);
    }
    input=sanitize(input);
    if(mode_==Mode::Client) {
        input_clock_+=network_elapsed;
        if(server_&&local_>=0&&input_clock_>=1.0/30.0) {
            input_clock_=std::fmod(input_clock_,1.0/30.0);
            auto out=packet(Message::Input);out.u32(next_input_++);out.f32(input.x);out.f32(input.z);out.u8(input.pulse?1:0);
            transport_.send(server_,net::kInputChannel,out.data());
        }
        if(server_&&(local_<0||!received_state_)&&clock_>5) disconnect("Handshake timeout");
        else if(server_&&received_state_&&clock_-last_state_>3) disconnect("Snapshot timeout");
    } else {
        inputs_[0]=input;
        for(int i=1;i<kPlayers;++i) {
            if(peers_[i]&&!ready_[i]&&clock_-joined_[i]>5) {
                transport_.disconnect(peers_[i],3);peers_[i]=0;
            }
            if(clock_-last_input_[i]>.25) inputs_[i]={};
        }
        const int steps=simulation_.advance(elapsed);
        for(int tick=0;tick<steps;++tick) {
            ++state_.tick;
            for(int i=0;i<kPlayers;++i) if(state_.actors[i].active) {
                auto& actor=state_.actors[i];
                actor.x=std::clamp(actor.x+inputs_[i].x*float(simulation_.step_seconds())*3,-7.0f,7.0f);
                actor.z=std::clamp(actor.z+inputs_[i].z*float(simulation_.step_seconds())*3,-5.0f,5.0f);
                actor.pulse=inputs_[i].pulse?1.0f:std::max(0.0f,actor.pulse-float(simulation_.step_seconds())*2);
            }
        }
        if(mode_==Mode::Host) {
            snapshot_clock_+=network_elapsed;
            if(snapshot_clock_>=1.0/20.0) {snapshot_clock_=std::fmod(snapshot_clock_,1.0/20.0);publish();}
        }
    }
    transport_.flush();
}
} // namespace beacon
