#include "catalog_model.hpp"
#include "artwork.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <unistd.h>

using namespace tyrell::catalog;
#define REQUIRE(value) do {if(!(value))throw std::runtime_error(#value);} while(false)
template<class F> void fails(F f){bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}REQUIRE(rejected);}
const std::string rows="PACKAGE\ttyrell-status@0.1.0-1\tTyrell Status\t0.1.0-1\t546044\tdownloadable\tRead-only device status\nPACKAGE\ttyrell-video@0.1.7-1\tTyrell Video\t0.1.7-1\t686346\tcached\tLocal video and captions\n";
const std::string validity="VALIDITY\t1700000000\t1700000100\n";
int main() {
    try {
        Model model;model.replace(parse_view("CATALOG\t1\tfresh\t7\tReady for verified downloads\n"+validity+rows));
        REQUIRE(model.snapshot.packages.size()==2&&model.can_download());
        REQUIRE(tyrell::app::command_errors(model.commands()).empty());
        REQUIRE(model.key('d')==Request::Download&&model.key('a')==Request::Artwork);
        model.key(key_down);REQUIRE(model.selected==1);model.key(key_enter);REQUIRE(model.page==Page::Details);
        model.key('q');REQUIRE(model.page==Page::List);model.key('h');REQUIRE(model.page==Page::Help);
        model.key('q');REQUIRE(model.page==Page::List);REQUIRE(model.key('q')==Request::Exit);
        model.busy=true;REQUIRE(!model.can_download()&&model.key('d')==Request::None&&model.key('r')==Request::None);
        REQUIRE(model.key('c')==Request::Cancel&&tyrell::app::command_errors(model.commands()).empty());
        model.busy=false;
        for(const auto state:{"expired","clock-unknown","incompatible","not-yet-valid","error","empty"}) {
            model.replace(parse_view(std::string("CATALOG\t1\t")+state+"\t7\tBrowse only\n"+rows));
            REQUIRE(!model.can_download()&&model.key('d')==Request::None&&model.key('a')==Request::None);
            REQUIRE(tyrell::app::command_errors(model.commands()).empty());
        }
        model.replace(parse_view("CATALOG\t1\tfresh\t8\tReady\n"+validity+rows));REQUIRE(model.selected==1);
        const auto video=model.current()->id;auto reordered=model.snapshot;std::swap(reordered.packages[0],reordered.packages[1]);model.replace(reordered);
        REQUIRE(model.selected==0&&model.current()->id==video);
        model.replace(parse_view("CATALOG\t1\tempty\t0\tNo cached catalog\n"));REQUIRE(model.current()==nullptr&&model.selected==0&&!model.can_download());
        fails([]{parse_view("CATALOG\t1\tfresh\t7\tIncomplete");});
        fails([]{parse_view("CATALOG\t2\tfresh\t7\tUnsupported\n");});
        fails([]{parse_view("CATALOG\t1\tfresh\t-1\tInvalid sequence\n");});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tUnsafe\x1b[31m\n");});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tReady\n"+rows+rows);});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tReady\nPACKAGE\tid@1\tName\t1\t18446744073709551615\tdownloadable\ttext\n");});
        fails([]{parse_view(std::string(65537,'x'));});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tMissing validity\n"+rows);});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tDuplicate validity\n"+validity+validity+rows);});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tInvalid interval\nVALIDITY\t1700000001\t1700000000\n"+rows);});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tOverflow interval\nVALIDITY\t1700000000\t9223372036854775808\n"+rows);});
        fails([]{parse_view("CATALOG\t1\tfresh\t1\tReady\nARTWORK\tunknown@1\t"+std::string(64,'a')+"\t100\t80\t80\t/tmp/a.png\n");});
        auto artwork=parse_view("CATALOG\t1\tfresh\t1\tReady\n"+validity+rows+"ARTWORK\ttyrell-status@0.1.0-1\t"+std::string(64,'a')+"\t100\t80\t80\t/tmp/a.png\n");
        REQUIRE(artwork.packages[0].artwork&&artwork.packages[0].artwork->width==80);
        model.replace(artwork);REQUIRE(model.can_download());
        model.invalidate_view("Signing key revoked; local verification failed");
        REQUIRE(model.snapshot.packages.size()==2&&model.snapshot.state=="error"&&!model.can_download());
        REQUIRE(model.state_label()=="UNVERIFIED / BROWSE ONLY"&&model.key('d')==Request::None&&model.key('a')==Request::None);
        for(const auto& item:model.snapshot.packages)REQUIRE(item.state=="browse-only"&&!item.artwork);
        model.replace(artwork);REQUIRE(model.can_download());
        REQUIRE(!model.revalidate_time(1700000000)&&model.can_download());
        REQUIRE(!model.revalidate_time(1700000099)&&model.can_download());
        REQUIRE(model.revalidate_time(1700000100)&&model.snapshot.state=="expired"&&!model.can_download());
        REQUIRE(model.key('d')==Request::None&&model.key('a')==Request::None);
        REQUIRE(!model.revalidate_time(1700000000)&&model.snapshot.state=="expired"&&!model.can_download());
        model.replace(artwork);REQUIRE(model.can_download());
        REQUIRE(model.revalidate_time(1699999999)&&model.snapshot.state=="not-yet-valid"&&!model.can_download());
        for(const auto unknown:{-1LL,0LL,1577836799LL}) {
            model.replace(artwork);REQUIRE(model.revalidate_time(unknown)&&model.snapshot.state=="clock-unknown"&&!model.can_download());
        }
        model.replace(artwork);model.snapshot.expires_at.reset();
        REQUIRE(!model.can_download()&&model.revalidate_time(1700000000)&&model.snapshot.state=="error");
        REQUIRE(parse_result("RESULT\t1\tok\tArchive verified\n",true)=="Archive verified");
        REQUIRE(parse_result("RESULT\t1\tok\tArchive verified\n",false).find("failed")!=std::string::npos);
        REQUIRE(parse_result("RESULT\t1\tcancelled\tCancelled\n",false)=="Cancelled");
        REQUIRE(size_text(546044)=="534 KiB"&&size_text(0)=="0 B");
        std::cout<<"Catalog model, input, freshness and response boundaries passed\n";return 0;
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
