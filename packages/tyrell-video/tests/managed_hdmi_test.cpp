#include "managed_hdmi_fixture.h"
#include "outputs.h"
#include <chrono>
#include <iostream>
#include <thread>

using namespace tyrell::video;
using managed_hdmi_fixture::Fixture;

static void require(bool value, const char* message) { if(!value) throw std::runtime_error(message); }
static const std::string acquire_op(32,'c'), use_op(32,'d'), cleanup_op(32,'e'), borrower(32,'f'), proof(32,'1');

static void acquire(ManagedHdmiSession& session) {
    std::string error;
    require(session.acquire(acquire_op,error),error.c_str());
}
static void drain(ManagedHdmiSession& session) {
    std::string error;
    require(session.register_borrower(borrower,error),error.c_str());
    const int graph_fd=session.borrow_fd(borrower,use_op,error);
    require(graph_fd>=0,error.c_str());
    close(graph_fd);
    require(session.seal(error),error.c_str());
    require(session.borrower_drained(borrower,use_op,proof,error),error.c_str());
}

static void successful_scoped_neutralization() {
    Fixture fixture;
    auto session=fixture.session();
    require(session->identity_json().find("\"planes\":\"14,15\"")!=std::string::npos,"Prepared scope unavailable");
    acquire(*session);
    require(session->acquired_json().find(acquire_op)!=std::string::npos,"Acquisition evidence lost operation binding");
    drain(*session);
    std::string error;
    require(session->release(cleanup_op,error),error.c_str());
    require(session->state()==ManagedHdmiState::Released,"Neutralized display not terminal");
    require(fixture.test_calls==1&&fixture.commit_calls==1&&fixture.terminal_calls==1&&fixture.drop_calls==1,"Incomplete backend sequence");
    require(fixture.value(14,"FB_ID")==0&&fixture.value(15,"CRTC_ID")==0&&fixture.value(12,"ACTIVE")==0&&fixture.value(11,"CRTC_ID")==0,"Owned scope not neutral");
    require(fixture.value(16,"FB_ID")==900&&fixture.value(16,"CRTC_ID")==99&&fixture.value(13,"CRTC_ID")==99,"Unrelated display modified");
    const auto evidence=session->evidence_json();
    require(evidence.find("\"fence\":true")!=std::string::npos&&evidence.find("\"future_writes_suppressed\":true")!=std::string::npos&&evidence.find(fixture.terminal_proof)!=std::string::npos,"Terminal facts missing");
    require(session->release(cleanup_op,error),"Terminal replay failed");
    session.reset();
    require(fixture.commit_calls==1&&fixture.terminal_calls==1&&fixture.drop_calls==1,"Destructor or replay repeated a mutation");
}

static void abandoned_and_failed_acquisition_never_restore() {
    {
        Fixture fixture;
        auto session=fixture.session();
        acquire(*session);
        session.reset();
        require(fixture.commit_calls==0&&fixture.drop_calls==0&&fixture.value(14,"FB_ID")==500,"Abandoned managed session restored or disabled display");
    }
    {
        Fixture fixture;
        fixture.atomic_supported=false;
        auto session=fixture.session();
        std::string error;
        require(!session->acquire(acquire_op,error)&&session->state()==ManagedHdmiState::Unknown,"Failed capability acquisition was not unknown");
        session.reset();
        require(fixture.commit_calls==0&&fixture.drop_calls==0,"Failed managed acquire used a rollback path");
    }
    {
        Fixture fixture;
        HdmiSession legacy; // Its ordinary empty destructor remains independent.
        require(fixture.commit_calls==0,"Default M5 session entered managed policy");
    }
}

static void borrowers_and_sealing_are_explicit() {
    Fixture fixture;
    auto session=fixture.session(); acquire(*session);
    std::string error;
    require(session->register_borrower(borrower,error),error.c_str());
    const int graph_fd=session->borrow_fd(borrower,use_op,error);
    require(graph_fd>=0,error.c_str());
    require(fcntl(graph_fd,F_GETFD)&FD_CLOEXEC,"Borrowed graph descriptor is inheritable");
    close(graph_fd); // A real close alone must not discharge the graph barrier.
    require(session->seal(error),error.c_str());
    require(!session->register_borrower(std::string(32,'2'),error),"Sealed borrower registered");
    require(session->borrow_fd(borrower,std::string(32,'3'),error)<0,"Sealed graph obtained another FD");
    require(!session->release(cleanup_op,error)&&fixture.commit_calls==0,"Borrowed graph was ignored");
    require(!session->borrower_drained(borrower,std::string(32,'3'),proof,error),"Foreign graph operation retired borrower");
    require(!session->borrower_drained(borrower,use_op,"closed",error),"FD close text certified graph completion");
    require(session->borrower_drained(borrower,use_op,proof,error),error.c_str());
    require(!session->release(acquire_op,error),"Acquisition token was reused as cleanup permit");
    require(session->release(cleanup_op,error),error.c_str());
}

static void changed_scope_refuses_mutation() {
    for(int scenario=0;scenario<3;++scenario) {
        Fixture fixture; auto session=fixture.session(); acquire(*session); drain(*session);
        if(scenario==0) fixture.set(12,"MODE_ID",999);
        if(scenario==1) fixture.set(13,"CRTC_ID",12);
        if(scenario==2) fixture.set(16,"CRTC_ID",12);
        std::string error;
        require(!session->release(cleanup_op,error)&&session->state()==ManagedHdmiState::Unknown,"Changed external KMS scope was accepted");
        require(fixture.commit_calls==0&&fixture.drop_calls==0,"External owner was overwritten");
    }
}

static void backend_failures_never_certify_release() {
    for(int scenario=0;scenario<7;++scenario) {
        Fixture fixture;
        if(scenario==0) fixture.fail_test=true;
        if(scenario==1) fixture.fail_commit=true;
        if(scenario==2) fixture.missing_fence=true;
        if(scenario==3) fixture.fail_fence=true;
        if(scenario==4) fixture.corrupt_readback=true;
        if(scenario==5) fixture.fail_drop=true;
        if(scenario==6) fixture.terminal_proof="unknown";
        auto session=fixture.session(); acquire(*session); drain(*session);
        std::string error;
        require(!session->release(cleanup_op,error)&&session->state()==ManagedHdmiState::Unknown,"Backend uncertainty became release");
        bool refused=false; try { session->evidence_json(); } catch(...) { refused=true; }
        require(refused,"Unknown backend issued terminal evidence");
        const int before=fixture.commit_calls;
        session.reset();
        require(fixture.commit_calls==before,"Unknown destructor started another KMS transaction");
    }
}

static void out_fence_is_not_terminal_completion() {
    Fixture fixture; fixture.hold_fence=true;
    int gate[2]; require(pipe(gate)==0,"Cannot create terminal gate");
    fixture.terminal_gate=[&]{ char value; require(read(gate[0],&value,1)==1,"Terminal gate closed"); };
    auto session=fixture.session(); acquire(*session); drain(*session);
    bool released=false; std::string error;
    std::thread worker([&]{ released=session->release(cleanup_op,error); });
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(3);
    while(!fixture.fence_ready&&std::chrono::steady_clock::now()<deadline) std::this_thread::yield();
    require(fixture.commit_calls==1&&session->state()==ManagedHdmiState::Releasing,"Atomic operation did not remain held");
    require(!fixture.terminal_entered,"Terminal hook ran before fence");
    fixture.signal_fence();
    while(!fixture.terminal_entered&&std::chrono::steady_clock::now()<deadline) std::this_thread::yield();
    require(fixture.terminal_entered&&session->state()==ManagedHdmiState::Releasing,"Out-fence incorrectly completed whole backend");
    bool refused=false; try { session->evidence_json(); } catch(...) { refused=true; }
    require(refused,"Evidence escaped while terminal completion was held");
    require(write(gate[1],"T",1)==1,"Cannot complete terminal gate");
    worker.join(); close(gate[0]); close(gate[1]);
    require(released&&session->state()==ManagedHdmiState::Released,error.c_str());
}

static void absent_qualification_refuses_before_side_effect() {
    Fixture fixture;
    auto session=std::make_unique<ManagedHdmiSession>(fixture.fd,fixture.scope(),fixture.binding());
    fixture.owns_fd=false;
    acquire(*session); drain(*session);
    std::string error;
    require(!session->release(cleanup_op,error)&&fixture.commit_calls==0&&fixture.drop_calls==0,"Missing backend qualification mutated KMS");
}

int main() {
    try {
        successful_scoped_neutralization();
        abandoned_and_failed_acquisition_never_restore();
        borrowers_and_sealing_are_explicit();
        changed_scope_refuses_mutation();
        backend_failures_never_certify_release();
        out_fence_is_not_terminal_completion();
        absent_qualification_refuses_before_side_effect();
        std::cout<<"PASS: managed KMS scope, borrower barrier, atomic/fence/readback, terminal hook and inert destructor\n";
        return 0;
    } catch(const std::exception& error) { std::cerr<<"Managed HDMI: "<<error.what()<<'\n'; return 1; }
}
