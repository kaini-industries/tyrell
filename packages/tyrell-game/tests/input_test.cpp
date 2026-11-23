#include "tyrell/game/input.hpp"
#include "raylib.h"
#include <iostream>
#include <stdexcept>
namespace {
void require(bool value,const char* description) { if(!value) throw std::runtime_error(description); }
void key(tyrell::game::NativeInputState& input,unsigned short code,int value) {input.feed(1,code,value);}
}
int main() {
    try {
    tyrell::game::NativeInputState input;
    input.begin_frame();key(input,17,1); // Linux W -> raylib W
    require(input.key_down(KEY_W)&&input.key_pressed(KEY_W),"W initial press");
    require(input.next_char()=='w'&&input.next_char()==0,"W printable queue");
    input.begin_frame();
    require(input.key_down(KEY_W)&&!input.key_pressed(KEY_W)&&!input.key_released(KEY_W),"held W survives polls without synthesized release");
    key(input,17,2);
    require(input.key_down(KEY_W)&&!input.key_pressed(KEY_W),"autorepeat preserves held state without new press edge");
    require(input.next_char()=='w',"autorepeat repeats text");
    key(input,17,0);
    require(!input.key_down(KEY_W)&&input.key_released(KEY_W),"real keyup releases W");
    input.begin_frame();key(input,57,1);key(input,57,0);
    require(input.key_pressed(KEY_SPACE)&&input.key_released(KEY_SPACE)&&!input.key_down(KEY_SPACE),"press/release in one poll preserves both edges");
    require(input.next_char()==' ',"space character");
    input.begin_frame();key(input,42,1);key(input,30,1);key(input,30,0);key(input,2,1);key(input,2,0);
    require(input.next_char()=='A'&&input.next_char()=='!',"Shift modifies letters and digits");
    key(input,42,0);key(input,58,1);key(input,58,0);key(input,30,1);key(input,30,0);
    require(input.next_char()=='A',"Caps Lock uppercase");
    key(input,42,1);key(input,30,1);key(input,30,0);key(input,42,0);
    require(input.next_char()=='a',"Shift plus Caps Lock lowercase");
    input.resync({},false);input.begin_frame();
    for(const auto code:{unsigned(11),unsigned(52),unsigned(2),unsigned(12)}) {key(input,static_cast<unsigned short>(code),1);key(input,static_cast<unsigned short>(code),0);}
    require(input.next_char()=='0'&&input.next_char()=='.'&&input.next_char()=='1'&&input.next_char()=='-',"IP/hostname characters");
    key(input,14,1);key(input,28,1);key(input,103,1);key(input,108,1);
    require(input.key_pressed(KEY_BACKSPACE)&&input.key_pressed(KEY_ENTER)&&input.key_down(KEY_UP)&&input.key_down(KEY_DOWN),"menu and editing keys map correctly");
    input.begin_frame();key(input,29,1);key(input,46,1);
    require(input.next_char()==0,"Ctrl shortcut does not append hostname text");
    input.resync({},false);input.begin_frame();key(input,17,1);key(input,32,1);input.next_char();input.next_char();
    input.feed(0,3,0); // SYN_DROPPED
    require(input.sync_needed(),"drop requests a kernel state resync");
    key(input,17,0);key(input,30,1);
    require(!input.key_down(KEY_W)&&!input.key_down(KEY_A)&&input.next_char()==0,"held controls cleared and events ignored until drop recovery snapshot");
    input.resync({30},false);
    require(!input.sync_needed()&&!input.key_down(KEY_W)&&!input.key_down(KEY_D)&&input.key_down(KEY_A),"kernel snapshot replaces stale held keys");
    require(!input.key_pressed(KEY_A)&&!input.key_released(KEY_W)&&input.next_char()==0,"resync does not create gameplay edges or text");
    input.begin_frame();key(input,30,0);
    require(input.key_released(KEY_A)&&!input.key_down(KEY_A),"key release works after resync");
    input.begin_frame();key(input,17,1);key(input,17,1);
    require(input.next_char()=='w'&&input.next_char()==0,"duplicate make does not duplicate text");
    input.begin_frame();require(input.next_char()==0,"unconsumed frame text is cleared before next poll");
    require(!input.key_down(-1)&&!input.key_pressed(10000)&&!input.key_released(0),"queries reject invalid key values");
    key(input,65535,1);key(input,17,3);
    require(input.next_char()==0,"unknown codes and malformed key values ignored");
    input.resync({},false);input.begin_frame();
    key(input,2,1);key(input,79,1);key(input,79,0);
    require(input.key_down(KEY_ONE)&&!input.key_down(KEY_KP_1),"Keypad release must not release a held top-row digit");
    require(input.next_char()=='1'&&input.next_char()=='1',"Both digit locations must produce text");
    key(input,28,1);key(input,96,1);key(input,28,0);
    require(input.key_down(KEY_KP_ENTER)&&!input.key_down(KEY_ENTER),"Enter locations must keep independent held state");
    // Native display transfer can take longer than a quick series of taps.
    // A menu must receive both Down taps before Enter, not one bool per key
    // queried later in the application's arbitrary key-enumeration order.
    input.resync({},false);input.begin_frame();
    key(input,108,1);key(input,108,0);key(input,108,1);key(input,108,0);key(input,28,1);key(input,28,0);
    require(input.next_key()==KEY_DOWN&&input.next_key()==KEY_DOWN&&input.next_key()==KEY_ENTER&&input.next_key()==0,"ordered queue retains repeated taps received in one poll");
    input.begin_frame();key(input,28,1);key(input,28,1);key(input,28,2);
    require(input.next_key()==KEY_ENTER&&input.next_key()==0,"duplicate make and autorepeat do not confirm menus again");
    key(input,28,0);key(input,108,1);input.begin_frame();
    require(input.next_key()==0,"unconsumed frame key events cleared before next poll");
    key(input,108,0);key(input,108,1);input.feed(0,3,0);
    require(input.next_key()==0,"SYN_DROPPED discards uncertain queued edges");
    key(input,28,1);input.resync({28},false);
    require(input.next_key()==0&&!input.key_pressed(KEY_ENTER),"resync held snapshot never confirms a menu");
    input.resync({},false);input.begin_frame();
    for(int i=0;i<200;++i){key(input,108,1);key(input,108,0);}
    for(int i=0;i<128;++i)require(input.next_key()==KEY_DOWN,"bounded queue retains its accepted prefix");
    require(input.next_key()==0,"key queue is bounded to 128 events");
    std::cout<<"Tyrell input tests passed\n";
    return 0;
    } catch(const std::exception& e) {std::cerr<<"input test: "<<e.what()<<'\n';return 1;}
}
