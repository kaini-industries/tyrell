#define main chess_application_main
#include "../src/main.cpp"
#undef main
#include "tyrell/game/input.hpp"
#define REQUIRE(x) do{if(!(x))throw std::runtime_error(std::string(__FILE__)+":"+std::to_string(__LINE__)+": " #x);}while(false)
struct Input {
 tyrell::game::NativeInputState state;
 int next_key(){return state.next_key();}bool down(int key){return state.key_down(key);}
 void frame(){state.begin_frame();}void event(int code,int value){state.feed(1,code,value);}
 void tap(int code){event(code,1);event(code,0);}
};
int main(){
 const auto dir=std::filesystem::temp_directory_path()/("chess-input-repeat-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
 try{
  {App app(dir/"burst");Input input;input.frame();input.tap(108);input.tap(108);input.tap(28);app.input(input,0);REQUIRE(app.page=="setup"&&app.setup.mode==Mode::Local);}
  {App app(dir/"held");Input input;input.frame();input.event(108,1);app.input(input,0);REQUIRE(app.menu==1);
   input.frame();for(int i=0;i<20;++i)input.event(108,2);app.input(input,.34);REQUIRE(app.menu==1);
   input.frame();app.input(input,.35);REQUIRE(app.menu==2);input.frame();app.input(input,10);REQUIRE(app.menu==3);
   input.frame();app.input(input,10.01);REQUIRE(app.menu==3);input.frame();input.event(108,0);app.input(input,11);REQUIRE(app.menu==3);
   input.frame();input.event(103,1);app.input(input,12);REQUIRE(app.menu==2);
   app.screen=Screen::Help;input.frame();app.input(input,20);REQUIRE(app.menu==2);app.screen=Screen::Home;
   input.frame();app.input(input,21);REQUIRE(app.menu==2);}
  {App app(dir/"confirm");Input input;input.frame();input.event(28,1);app.input(input,0);REQUIRE(app.screen==Screen::Setup);
   for(int i=1;i<8;++i){input.frame();input.event(28,2);app.input(input,i);}REQUIRE(app.page=="setup"&&!app.haveGame);}
  {App app(dir/"text");app.screen=Screen::Network;app.page="address";app.networkAddress="123";Input input;input.frame();input.event(14,1);app.input(input,0);REQUIRE(app.networkAddress=="12");input.frame();app.input(input,.35);REQUIRE(app.networkAddress=="1");input.frame();app.input(input,.46);REQUIRE(app.networkAddress.empty());input.frame();input.event(14,0);app.input(input,1);REQUIRE(app.networkAddress.empty());}
  {App app(dir/"selection");app.setup.mode=Mode::Local;app.startGame();Input input;input.frame();input.event(28,1);app.input(input,0);REQUIRE(app.selected.valid());input.frame();input.event(28,2);app.input(input,2);REQUIRE(app.game.history.empty()&&app.selected.valid());}
  std::filesystem::remove_all(dir);std::puts("Ordered input passed: rapid event bursts, deliberate held navigation, bounded stalls, release/context resets, safe confirmations and IP backspace repeat.");return 0;
 }catch(const std::exception& e){std::filesystem::remove_all(dir);std::fprintf(stderr,"%s\n",e.what());return 1;}
}
