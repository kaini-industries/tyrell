#include "artwork.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <unistd.h>

using namespace tyrell::catalog;
#define REQUIRE(value) do {if(!(value))throw std::runtime_error(#value);} while(false)
template<class F>void fails(F f){bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}REQUIRE(rejected);}
int main() {
    try {
        const auto original=std::filesystem::path(CATALOG_TEST_ICON);
        Artwork descriptor{CATALOG_TEST_ICON_SHA,original.string(),unsigned(std::filesystem::file_size(original)),80,80};
        auto bytes=load_artwork(descriptor);REQUIRE(bytes.size()==descriptor.bytes);
        auto bad=descriptor;bad.sha256[0]=bad.sha256[0]=='a'?'b':'a';fails([&]{load_artwork(bad);});
        bad=descriptor;bad.width=81;fails([&]{load_artwork(bad);});
        bad=descriptor;bad.height=129;fails([&]{load_artwork(bad);});
        bad=descriptor;bad.bytes=65537;fails([&]{load_artwork(bad);});
        const auto root=std::filesystem::temp_directory_path()/std::filesystem::path("catalog-artwork-"+std::to_string(::getpid()));
        REQUIRE(!std::filesystem::exists(root));std::filesystem::create_directory(root);
        struct Cleanup{std::filesystem::path path;~Cleanup(){std::filesystem::remove_all(path);}} cleanup{root};
        std::filesystem::create_symlink(original,root/"link.png");bad=descriptor;bad.path=(root/"link.png").string();fails([&]{load_artwork(bad);});
        const auto copied=root/"copy.png";std::filesystem::copy_file(original,copied);descriptor.path=copied.string();
        auto retained=load_artwork(descriptor);
        {std::fstream output(copied,std::ios::in|std::ios::out|std::ios::binary);output.seekp(35);output.put('x');}
        REQUIRE(retained==bytes);fails([&]{load_artwork(descriptor);});
        std::cout<<"Artwork bounded-file, hash, dimensions, symlink and immutable-memory tests passed\n";return 0;
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
