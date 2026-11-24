#include "catalog_model.hpp"

#include <algorithm>
#include <charconv>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>

namespace tyrell::catalog {
namespace {
std::vector<std::string> fields(const std::string& line) {
    std::vector<std::string> result;
    std::size_t at=0;
    for(;;) {
        const auto end=line.find('\t',at);
        result.push_back(line.substr(at,end==std::string::npos?end:end-at));
        if(end==std::string::npos) return result;
        at=end+1;
    }
}
void text(const std::string& value,std::size_t maximum,bool empty=false) {
    if((!empty && value.empty()) || value.size()>maximum ||
       std::any_of(value.begin(),value.end(),[](unsigned char c){return c<32 || c>126;}))
        throw std::runtime_error("Malformed catalog display response");
}
std::uint64_t number(const std::string& value) {
    std::uint64_t result=0;
    if(value.empty() || value.size()>20) throw std::runtime_error("Invalid catalog size or sequence");
    const auto parsed=std::from_chars(value.data(),value.data()+value.size(),result);
    if(parsed.ec!=std::errc{} || parsed.ptr!=value.data()+value.size())
        throw std::runtime_error("Invalid catalog size or sequence");
    return result;
}
}
Snapshot parse_view(const std::string& data) {
    if(data.empty() || data.size()>65536 || data.back()!='\n')
        throw std::runtime_error("Incomplete or oversized catalog response");
    std::istringstream lines(data);std::string line;
    if(!std::getline(lines,line)) throw std::runtime_error("Missing catalog response");
    const auto header=fields(line);
    if(header.size()!=5 || header[0]!="CATALOG" || header[1]!="1")
        throw std::runtime_error("Unsupported catalog display response");
    const std::set<std::string> states={"fresh","expired","not-yet-valid","clock-unknown","empty","error","incompatible"};
    if(!states.count(header[2])) throw std::runtime_error("Unknown catalog freshness state");
    number(header[3]);text(header[4],160,true);
    Snapshot result;result.state=header[2];result.sequence=header[3];result.message=header[4];
    std::set<std::string> ids;
    while(std::getline(lines,line)) {
        const auto row=fields(line);
        if(row.size()==3 && row[0]=="VALIDITY") {
            const auto issued=number(row[1]),expires=number(row[2]);
            if(result.issued_at || issued<1577836800 || expires<=issued ||
               expires>static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()))
                throw std::runtime_error("Invalid or duplicated catalog validity interval");
            result.issued_at=issued;result.expires_at=expires;continue;
        }
        if(row.size()==7 && row[0]=="ARTWORK") {
            const auto found=std::find_if(result.packages.begin(),result.packages.end(),[&](const auto& p){return p.id==row[1];});
            const auto bytes=number(row[3]),w=number(row[4]),h=number(row[5]);
            if(found==result.packages.end() || found->artwork || row[2].size()!=64 ||
               std::any_of(row[2].begin(),row[2].end(),[](char c){return !((c>='0'&&c<='9')||(c>='a'&&c<='f'));}) ||
               bytes<33 || bytes>65536 || w<1 || w>128 || h<1 || h>128 || row[6].empty() || row[6][0]!='/' || row[6].size()>1024 ||
               std::any_of(row[6].begin(),row[6].end(),[](unsigned char c){return c<32 || c==127;}))
                throw std::runtime_error("Invalid catalog artwork descriptor");
            found->artwork=Artwork{row[2],row[6],unsigned(bytes),unsigned(w),unsigned(h)};continue;
        }
        if(row.size()!=7 || row[0]!="PACKAGE" || result.packages.size()>=32)
            throw std::runtime_error("Malformed catalog package list");
        text(row[1],164);text(row[2],48);text(row[3],96);text(row[6],120,true);
        const auto bytes=number(row[4]);
        if(bytes>8ULL*1024*1024*1024 || !ids.insert(row[1]).second || row[1].find('@')==0 ||
           std::count(row[1].begin(),row[1].end(),'@')!=1 || row[1].back()=='@' || row[1].front()=='-' ||
           std::any_of(row[1].begin(),row[1].end(),[](unsigned char c){
               return !((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||std::string("@.+~:-").find(char(c))!=std::string::npos);
           }) || (row[5]!="downloadable" && row[5]!="cached" && row[5]!="browse-only"))
            throw std::runtime_error("Invalid catalog package identity or eligibility");
        result.packages.push_back({row[1],row[2],row[3],row[5],row[6],bytes,std::nullopt});
    }
    if(result.state=="fresh" && (!result.issued_at || !result.expires_at || result.packages.empty()))
        throw std::runtime_error("Fresh catalog response requires validity and package rows");
    return result;
}
std::string parse_result(const std::string& data,bool success) {
    if(data.empty() || data.size()>65536 || data.back()!='\n')
        return success?"Operation finished; reloading catalog":"Operation failed; previous catalog retained";
    auto line=data.substr(0,data.size()-1);
    if(line.find('\n')!=std::string::npos) return "Backend returned an invalid operation result";
    const auto row=fields(line);
    if(row.size()!=4 || row[0]!="RESULT" || row[1]!="1") return "Backend returned an invalid operation result";
    try {text(row[2],32);text(row[3],160,true);} catch(...) {return "Backend returned an invalid operation result";}
    if((success && row[2]!="ok") || (!success && row[2]!="error" && row[2]!="cancelled"))
        return "Operation failed; previous catalog retained";
    return row[3].empty()?(success?"Operation complete":"Operation failed"):row[3];
}
std::string size_text(std::uint64_t bytes) {
    if(bytes<1024) return std::to_string(bytes)+" B";
    if(bytes<1024*1024) return std::to_string((bytes+1023)/1024)+" KiB";
    return std::to_string((bytes+1024*1024-1)/(1024*1024))+" MiB";
}
void Model::replace(Snapshot next) {
    const auto old=current()?current()->id:std::string{};
    if(snapshot.state=="error" && notice==snapshot.message)notice.clear();
    snapshot=std::move(next);
    const auto found=std::find_if(snapshot.packages.begin(),snapshot.packages.end(),[&](const auto& p){return p.id==old;});
    if(found!=snapshot.packages.end()) selected=std::size_t(found-snapshot.packages.begin());
    else if(snapshot.packages.empty()) selected=0;
    else selected=std::min(selected,snapshot.packages.size()-1);
}
void Model::invalidate_view(const std::string& message) {
    snapshot.state="error";snapshot.message=message;notice=message;
    snapshot.issued_at.reset();snapshot.expires_at.reset();
    for(auto& item:snapshot.packages){item.state="browse-only";item.artwork.reset();}
}
bool Model::revalidate_time(std::int64_t wall_epoch) {
    if(snapshot.state!="fresh")return false;
    std::string state,message;
    if(!snapshot.issued_at || !snapshot.expires_at) {
        invalidate_view("Catalog validity is unavailable; browse only");return true;
    }
    if(wall_epoch<1577836800) {
        state="clock-unknown";message="Clock unknown; browse only";
    } else if(static_cast<std::uint64_t>(wall_epoch)<*snapshot.issued_at) {
        state="not-yet-valid";message="Catalog not yet valid; check the clock";
    } else if(static_cast<std::uint64_t>(wall_epoch)>=*snapshot.expires_at) {
        state="expired";message="Catalog expired; browse only";
    } else return false;
    snapshot.state=std::move(state);snapshot.message=message;notice=message;
    for(auto& item:snapshot.packages)item.state="browse-only";
    return true;
}
const Package* Model::current() const {return selected<snapshot.packages.size()?&snapshot.packages[selected]:nullptr;}
bool Model::can_download() const {
    const auto* item=current();
    return !busy && snapshot.state=="fresh" && snapshot.issued_at && snapshot.expires_at && item && (item->state=="downloadable" || item->state=="cached");
}
std::vector<tyrell::app::Command<Action>> Model::commands() const {
    using C=tyrell::app::Command<Action>;
    std::vector<C> result={
        {Action::Up,{key_up,'k','K'},"Up","Select","Previous package"},
        {Action::Down,{key_down,'j','J'},"Down","Select","Next package"},
        {Action::Details,{key_enter},"Enter","Details","Package details"},
        {Action::Refresh,{'r','R'},"R","Refresh","Refresh signed catalog",!busy,tyrell::app::Repeat::PressOnly,tyrell::app::Confirmation::None,"An operation is running"},
        {Action::Download,{'d','D'},"D","Download","Download verified archive",can_download(),tyrell::app::Repeat::PressOnly,tyrell::app::Confirmation::None,"Fresh compatible catalog required"},
        {Action::Artwork,{'a','A'},"A","Artwork","Cache verified artwork",can_download(),tyrell::app::Repeat::PressOnly,tyrell::app::Confirmation::None,"Fresh compatible catalog required"},
        {Action::Help,{'h','H','?'},"H","Help","Catalog controls"},
        {Action::Cancel,{'c','C'},"C","Cancel","Cancel current operation",busy,tyrell::app::Repeat::PressOnly,tyrell::app::Confirmation::None,"No operation is running"},
        {Action::Exit,{key_escape,'q','Q'},"Q","Exit","Back to launcher"}};
    return result;
}
Request Model::key(int code) {
    const auto table=commands();const auto* command=tyrell::app::find_command(table,code);
    if(!command) return Request::None;
    if(!command->enabled) {notice=command->unavailable_reason;return Request::None;}
    switch(command->id) {
        case Action::Up: if(selected>0)--selected;break;
        case Action::Down: if(selected+1<snapshot.packages.size())++selected;break;
        case Action::Details: if(current())page=page==Page::Details?Page::List:Page::Details;break;
        case Action::Help: page=page==Page::Help?Page::List:Page::Help;break;
        case Action::Refresh:return Request::Refresh;
        case Action::Download:return Request::Download;
        case Action::Artwork:return Request::Artwork;
        case Action::Cancel:return Request::Cancel;
        case Action::Exit: if(page!=Page::List){page=Page::List;break;}return Request::Exit;
    }
    return Request::None;
}
std::string Model::state_label() const {
    if(snapshot.state=="fresh")return "VERIFIED / FRESH";
    if(snapshot.state=="expired")return "EXPIRED / BROWSE ONLY";
    if(snapshot.state=="clock-unknown")return "CLOCK UNKNOWN / BROWSE ONLY";
    if(snapshot.state=="incompatible")return "OTHER BASE / BROWSE ONLY";
    if(snapshot.state=="not-yet-valid")return "NOT YET VALID / BROWSE ONLY";
    if(snapshot.state=="error")return "UNVERIFIED / BROWSE ONLY";
    return "OFFLINE CATALOG";
}
} // namespace tyrell::catalog
