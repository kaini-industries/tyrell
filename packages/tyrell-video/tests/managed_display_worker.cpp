// Controlled, uninstalled native fixture for the Python authority integration.
// Every display call below is the production managed class. Only the hardware
// boundaries/finality provider are fixtures; no device node or legacy helper
// is opened. This binary is built only with BUILD_TESTING.
#include "managed_hdmi_fixture.h"
#include "managed_lcd_fixture.h"
#include "json_string.h"
#include <iostream>
#include <map>
#include <sstream>

using namespace tyrell::video;

static void require(bool value,const std::string& message) { if(!value) throw std::runtime_error(message); }
static void emit(const std::string& value) { std::cout<<value<<'\n'<<std::flush; }
static bool id(const std::string& value) {
    return value.size()==32&&value.find_first_not_of("0123456789abcdef")==std::string::npos;
}
static std::string lcd_json(const ManagedLcdEvidence& e,const char* stage,const std::string& operation={}) {
    std::ostringstream out;
    out<<"{\"kind\":\"lcd-v3\",\"epoch\":"<<e.epoch<<",\"resource\":"<<json_string(e.resource)
       <<",\"generation\":"<<json_string(e.generation)<<",\"backend\":\"controlled-memory-v3\",\"width\":320,\"height\":170,\"format\":\"RGB565\""
       <<",\"stage\":"<<json_string(stage)<<",\"operation\":"<<json_string(operation)
       <<",\"acquired\":"<<(e.acquired?"true":"false")<<",\"sealed\":"<<(e.sealed?"true":"false")
       <<",\"queued\":"<<e.queued<<",\"inflight\":"<<e.inflight<<",\"driver_finality\":"<<(e.driver_finality?"true":"false")
       <<",\"future_callbacks_disabled\":"<<(e.future_callbacks_disabled?"true":"false")
       <<",\"backend_receipt\":"<<json_string(e.backend_receipt)<<'}';
    return out.str();
}
static const char* operation_state(ManagedLcdOperationState state) {
    switch(state) {
        case ManagedLcdOperationState::Queued:return "queued";
        case ManagedLcdOperationState::Active:return "active";
        case ManagedLcdOperationState::Pending:return "pending";
        case ManagedLcdOperationState::Complete:return "complete";
        case ManagedLcdOperationState::Suppressed:return "suppressed-before-start";
        case ManagedLcdOperationState::Unknown:return "unknown";
        default:return "missing";
    }
}

int main(int argc,char** argv) {
    try {
        require(argc==7,"Explicit controlled display bootstrap required");
        const std::string domain=argv[1],resource=argv[3],generation=argv[4],mode=argv[6];
        const auto epoch=std::stoull(argv[2]);
        const int gate=std::stoi(argv[5]);
        require((domain=="kms"||domain=="lcd")&&epoch>0&&id(resource)&&id(generation)&&
                (mode=="ok"||mode=="readback-fail"||mode=="unqualified"),"Invalid fixed display fixture");
        std::unique_ptr<managed_hdmi_fixture::Fixture> drm;
        std::unique_ptr<ManagedHdmiSession> hdmi;
        std::shared_ptr<test::ControlledLcdBackend> memory;
        std::unique_ptr<ManagedLcd> lcd;
        std::map<std::string,int> graph_fds;
        const ManagedLcdScope lcd_scope{epoch,resource,generation};
        if(domain=="kms") {
            drm=std::make_unique<managed_hdmi_fixture::Fixture>();
            if(mode=="readback-fail") drm->corrupt_readback=true;
            if(mode=="unqualified") drm->terminal_proof="unknown";
            if(gate>2) drm->terminal_gate=[&] {
                emit("{\"event\":\"terminal-pending\"}");
                char byte; require(read(gate,&byte,1)==1&&byte=='G',"Controlled terminal gate lost");
            };
            hdmi=drm->session({epoch,resource,generation});
            emit(hdmi->identity_json());
        } else {
            lv_init();
            memory=std::make_shared<test::ControlledLcdBackend>(lcd_scope);
            lcd=std::make_unique<ManagedLcd>(lcd_scope,memory);
            emit(lcd_json(lcd->evidence(),"prepared"));
        }
        char bytes[1025];
        while(std::cin.getline(bytes,sizeof(bytes))) {
            std::istringstream line(bytes);
            std::vector<std::string> words;std::string item;
            while(line>>item) words.push_back(item);
            require(!words.empty(),"Empty native fixture command");
            std::string error;
            const auto& command=words[0];
            if(command=="acquire"&&words.size()==2&&id(words[1])) {
                if(hdmi) { require(hdmi->acquire(words[1],error),error);emit(hdmi->acquired_json()); }
                else { require(lcd->create(error),error);emit(lcd_json(lcd->evidence(),"acquired",words[1])); }
            } else if(command=="borrow"&&words.size()==3&&id(words[1])&&id(words[2])&&hdmi) {
                require(hdmi->register_borrower(words[1],error),error);
                const int fd=hdmi->borrow_fd(words[1],words[2],error);require(fd>2,error);
                graph_fds[words[1]]=fd;
                emit("{\"borrower\":"+json_string(words[1])+",\"operation\":"+json_string(words[2])+",\"admitted\":true}");
            } else if(command=="drain"&&words.size()==4&&id(words[1])&&id(words[2])&&id(words[3])&&hdmi) {
                require(graph_fds.count(words[1])!=0,"Unknown graph borrower");
                close(graph_fds.at(words[1]));graph_fds.erase(words[1]);
                require(hdmi->borrower_drained(words[1],words[2],words[3],error),error);
                emit("{\"borrower\":"+json_string(words[1])+",\"operation\":"+json_string(words[2])+",\"drained\":true,\"receipt\":"+json_string(words[3])+"}");
            } else if(command=="submit"&&words.size()==2&&id(words[1])&&lcd) {
                require(lcd->submit(epoch,words[1],error),error);emit("{\"submitted\":true}");
            } else if(command=="refresh"&&words.size()==1&&lcd) {
                require(lcd->refresh(epoch,error),error);emit(lcd_json(lcd->evidence(),"pending"));
            } else if(command=="seal"&&words.size()==1) {
                require(hdmi?hdmi->seal(error):lcd->seal(epoch,error),error);emit("{\"sealed\":true}");
            } else if(command=="complete"&&words.size()==1&&lcd) {
                memory->allow_complete=true;require(lcd->poll(epoch,error),error);emit(lcd_json(lcd->evidence(),"polled"));
            } else if(command=="operation"&&words.size()==2&&id(words[1])&&lcd) {
                const auto value=lcd->operation_evidence(words[1]);
                emit("{\"operation\":"+json_string(value.operation)+",\"state\":"+json_string(operation_state(value.state))+
                     ",\"receipt\":"+json_string(value.backend_receipt)+"}");
            } else if(command=="release"&&words.size()==2&&id(words[1])) {
                if(hdmi) { require(hdmi->release(words[1],error),error);emit(hdmi->evidence_json()); }
                else {
                    memory->allow_finality=mode!="unqualified";
                    const auto proof=lcd->release(epoch,error);require(bool(proof),error);
                    emit(lcd_json(*proof,"released",words[1]));
                }
            } else if(command=="fence"&&words.size()==1) {
                require(graph_fds.empty(),"Live graph prevents native fence");
                if(hdmi) require(hdmi->state()==ManagedHdmiState::Released,"KMS not terminal");
                if(lcd) require(lcd->evidence().driver_finality,"LCD not terminal");
                const unsigned writes=memory?memory->writes.load():0;
                hdmi.reset();lcd.reset();
                if(memory) { require(memory->writes==writes,"Destructor wrote pixels");lv_deinit(); }
                emit("{\"fenced\":true}");return 0;
            } else throw std::runtime_error("Unknown or malformed fixed native command");
        }
        for(const auto& item:graph_fds) close(item.second);
        return 3; // EOF is abandonment, never a success/fence acknowledgment.
    } catch(const std::exception& error) {
        std::cerr<<"Controlled managed display: "<<error.what()<<'\n';
        emit("{\"failed\":true}");return 2;
    }
}
