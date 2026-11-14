#include "tyrell/game/input.hpp"
#include <utility>

namespace tyrell::game {
namespace {
bool valid_key(int key) { return key>0 && key<512; }
}

void NativeInputState::begin_frame() {
    pressed_.fill(false); released_.fill(false); chars_.clear();
}
int NativeInputState::map_key(unsigned short code) {
    // Alphabetic physical positions in Linux input-event-codes.h.
    constexpr std::pair<unsigned short,int> letters[]={
        {16,'Q'},{17,'W'},{18,'E'},{19,'R'},{20,'T'},{21,'Y'},{22,'U'},{23,'I'},{24,'O'},{25,'P'},
        {30,'A'},{31,'S'},{32,'D'},{33,'F'},{34,'G'},{35,'H'},{36,'J'},{37,'K'},{38,'L'},
        {44,'Z'},{45,'X'},{46,'C'},{47,'V'},{48,'B'},{49,'N'},{50,'M'}};
    for(const auto& entry:letters) if(entry.first==code) return entry.second;
    if(code>=2 && code<=10) return '1'+code-2;
    if(code==11) return '0';
    switch(code) {
        case 1:return 256; // Escape
        case 12:return '-';case 13:return '=';case 14:return 259; // Backspace
        case 15:return 258;case 26:return '[';case 27:return ']';case 28:return 257;
        case 29:return 341;case 39:return ';';case 40:return '\'';case 41:return '`';
        case 42:return 340;case 43:return '\\';case 51:return ',';case 52:return '.';case 53:return '/';
        case 54:return 344;case 56:return 342;case 57:return ' ';case 58:return 280;
        case 97:return 345;case 100:return 346;case 102:return 268;
        case 103:return 265;case 104:return 266;case 105:return 263;case 106:return 262;
        case 107:return 269;case 108:return 264;case 109:return 267;case 110:return 260;case 111:return 261;
        case 125:return 343;case 126:return 347;
        // Preserve independent physical key identities for keypad keys. Aliasing
        // them to digits/Enter would let one key's release clear another key.
        case 71:return 327;case 72:return 328;case 73:return 329;case 74:return 333;
        case 75:return 324;case 76:return 325;case 77:return 326;case 78:return 334;
        case 79:return 321;case 80:return 322;case 81:return 323;case 82:return 320;
        case 83:return 330;case 96:return 335;case 98:return 331;case 55:return 332;case 117:return 336;
        default:return 0;
    }
}
int NativeInputState::ascii(int key) const {
    // Ctrl/Alt/Meta shortcuts are not hostname text.
    if(down_[341] || down_[345] || down_[342] || down_[346] || down_[343] || down_[347]) return 0;
    const bool shift=down_[340] || down_[344];
    if(key>=320 && key<=329) return '0'+key-320;
    switch(key) {
        case 330:return '.';case 331:return '/';case 332:return '*';
        case 333:return '-';case 334:return '+';case 336:return '=';
        default:break;
    }
    if(key>='A' && key<='Z') return (shift!=caps_)?key:key+('a'-'A');
    if(key>='0' && key<='9') return shift?")!@#$%^&*("[key-'0']:key;
    const std::string plain="-=[]\\;',./`", shifted="_+{}|:\"<>?~";
    const auto position=plain.find(char(key));
    if(position!=std::string::npos) return shift?shifted[position]:key;
    if(key==' ' || key=='+') return key;
    return 0;
}
void NativeInputState::feed(std::uint16_t type,std::uint16_t code,std::int32_t value) {
    if(type==0 && code==3) { // EV_SYN / SYN_DROPPED
        // Stop motion while input is uncertain. Poll restores the actual held
        // keys with EVIOCGKEY after the next SYN_REPORT, without press edges.
        syncing_=true; down_.fill(false); pressed_.fill(false); released_.fill(false); chars_.clear();
        return;
    }
    if(syncing_ || type!=1 || value<0 || value>2) return;
    const int key=map_key(code);
    if(!valid_key(key)) return;
    if(value==0) {
        if(down_[key]) released_[key]=true;
        down_[key]=false;
    } else if(value==1) {
        if(down_[key]) return; // Duplicate makes are not extra edges/text.
        down_[key]=true; pressed_[key]=true;
        if(key==280) caps_=!caps_;
        const int c=ascii(key);
        if(c && chars_.size()<128) chars_.push_back(c);
    } else if(down_[key]) {
        // Linux autorepeat repeats printable characters without creating a
        // second press edge or releasing the held game-control key.
        const int c=ascii(key);
        if(c && chars_.size()<128) chars_.push_back(c);
    }
}
void NativeInputState::resync(const std::vector<unsigned short>& held_codes,bool caps_lock) {
    down_.fill(false); pressed_.fill(false); released_.fill(false); chars_.clear();
    for(const auto code:held_codes) { const int key=map_key(code); if(valid_key(key)) down_[key]=true; }
    caps_=caps_lock; syncing_=false;
}
bool NativeInputState::sync_needed() const { return syncing_; }
bool NativeInputState::key_down(int key) const { return valid_key(key) && down_[key]; }
bool NativeInputState::key_pressed(int key) const { return valid_key(key) && pressed_[key]; }
bool NativeInputState::key_released(int key) const { return valid_key(key) && released_[key]; }
int NativeInputState::next_char() { if(chars_.empty()) return 0; const int c=chars_.front();chars_.pop_front();return c; }

} // namespace tyrell::game
