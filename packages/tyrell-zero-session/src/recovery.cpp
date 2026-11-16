// Fixed native recovery display. No login, command entry or network control.
#include <array>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <linux/fb.h>
#include <linux/kd.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <termios.h>
#include <unistd.h>
#include <vector>

namespace {
constexpr int width = 320, height = 170;
volatile std::sig_atomic_t stopped = 0;
void stop(int) { stopped = 1; }
std::array<uint8_t, 7> glyph(char c) {
    switch (c) {
    case 'A': return {14,17,17,31,17,17,17};
    case 'B': return {30,17,17,30,17,17,30};
    case 'C': return {14,17,16,16,16,17,14};
    case 'D': return {30,17,17,17,17,17,30};
    case 'E': return {31,16,16,30,16,16,31};
    case 'F': return {31,16,16,30,16,16,16};
    case 'G': return {14,17,16,23,17,17,15};
    case 'H': return {17,17,17,31,17,17,17};
    case 'I': return {14,4,4,4,4,4,14};
    case 'J': return {7,2,2,2,18,18,12};
    case 'K': return {17,18,20,24,20,18,17};
    case 'L': return {16,16,16,16,16,16,31};
    case 'M': return {17,27,21,21,17,17,17};
    case 'N': return {17,25,21,19,17,17,17};
    case 'O': return {14,17,17,17,17,17,14};
    case 'P': return {30,17,17,30,16,16,16};
    case 'Q': return {14,17,17,17,21,18,13};
    case 'R': return {30,17,17,30,20,18,17};
    case 'S': return {15,16,16,14,1,1,30};
    case 'T': return {31,4,4,4,4,4,4};
    case 'U': return {17,17,17,17,17,17,14};
    case 'V': return {17,17,17,17,17,10,4};
    case 'W': return {17,17,17,21,21,21,10};
    case 'X': return {17,17,10,4,10,17,17};
    case 'Y': return {17,17,10,4,4,4,4};
    case 'Z': return {31,1,2,4,8,16,31};
    default: return {};
    }
}

std::vector<uint32_t> render() {
    std::vector<uint32_t> pixels(width * height, 0x101820);
    const std::array<std::string, 6> lines = {
        "TYRELL ZERO", "RECOVERY", "", "USE CONFIGURED SSH", "OR BOOT A KNOWN", "REFERENCE CARD"};
    for (size_t line = 0; line < lines.size(); ++line) {
        const int x0 = (width - static_cast<int>(lines[line].size()) * 12) / 2;
        const int y0 = 17 + static_cast<int>(line) * 23;
        for (size_t column = 0; column < lines[line].size(); ++column) {
            const auto bits = glyph(lines[line][column]);
            for (int row = 0; row < 7; ++row)
                for (int x = 0; x < 5; ++x)
                    if (bits[row] & (1u << (4 - x)))
                        for (int sy = 0; sy < 2; ++sy)
                            for (int sx = 0; sx < 2; ++sx)
                                pixels[(y0 + row * 2 + sy) * width + x0 + static_cast<int>(column) * 12 + x * 2 + sx] = line == 1 ? 0xeab66a : 0xe5edf3;
        }
    }
    return pixels;
}

class Terminal {
    bool have_term = false, have_kd = false;
    termios previous{};
    int previous_kd = KD_TEXT;
public:
    Terminal() {
        if (!isatty(STDIN_FILENO)) return;
        have_term = tcgetattr(STDIN_FILENO, &previous) == 0;
        if (have_term) {
            auto raw = previous;
            cfmakeraw(&raw);
            tcsetattr(STDIN_FILENO, TCSANOW, &raw);
        }
        have_kd = ioctl(STDIN_FILENO, KDGETMODE, &previous_kd) == 0;
        if (have_kd) ioctl(STDIN_FILENO, KDSETMODE, KD_GRAPHICS);
    }
    ~Terminal() {
        if (have_kd) ioctl(STDIN_FILENO, KDSETMODE, previous_kd);
        if (have_term) tcsetattr(STDIN_FILENO, TCSANOW, &previous);
    }
};

uint32_t channel(uint32_t rgb, int shift, const fb_bitfield &field) {
    if (!field.length) return 0;
    const auto component = (rgb >> shift) & 0xff;
    return (component * ((1u << field.length) - 1u) / 255u) << field.offset;
}

bool framebuffer(const std::vector<uint32_t> &pixels) {
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    fb_var_screeninfo variable{};
    fb_fix_screeninfo fixed{};
    if (ioctl(fd, FBIOGET_VSCREENINFO, &variable) || ioctl(fd, FBIOGET_FSCREENINFO, &fixed) ||
        (variable.bits_per_pixel != 16 && variable.bits_per_pixel != 32) ||
        variable.xres < width || variable.yres < height ||
        variable.xres > 8192 || variable.yres > 8192 || fixed.smem_len > 256u * 1024u * 1024u ||
        variable.red.length > 8 || variable.green.length > 8 || variable.blue.length > 8 ||
        variable.red.offset > variable.bits_per_pixel || variable.red.length > variable.bits_per_pixel - variable.red.offset ||
        variable.green.offset > variable.bits_per_pixel || variable.green.length > variable.bits_per_pixel - variable.green.offset ||
        variable.blue.offset > variable.bits_per_pixel || variable.blue.length > variable.bits_per_pixel - variable.blue.offset) {
        close(fd); return false;
    }
    void *mapping = mmap(nullptr, fixed.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (mapping == MAP_FAILED) { close(fd); return false; }
    const unsigned bytes = variable.bits_per_pixel / 8;
    bool success = true;
    for (unsigned y = 0; y < variable.yres && success; ++y)
        for (unsigned x = 0; x < variable.xres; ++x) {
            const size_t offset = (static_cast<size_t>(y) + variable.yoffset) * fixed.line_length + (static_cast<size_t>(x) + variable.xoffset) * bytes;
            if (offset > fixed.smem_len || bytes > fixed.smem_len - offset) { success = false; break; }
            const uint32_t rgb = pixels[(static_cast<size_t>(y) * height / variable.yres) * width + static_cast<size_t>(x) * width / variable.xres];
            uint32_t value = channel(rgb, 16, variable.red) | channel(rgb, 8, variable.green) | channel(rgb, 0, variable.blue);
            std::memcpy(static_cast<char *>(mapping) + offset, &value, bytes);
        }
    munmap(mapping, fixed.smem_len);
    close(fd);
    return success;
}
}

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--version") { std::cout << "0.2.0\n"; return 0; }
    const auto pixels = render();
    if (argc == 3 && std::string(argv[1]) == "--render-ppm") {
        std::ofstream output(argv[2], std::ios::binary);
        output << "P6\n320 170\n255\n";
        for (uint32_t rgb : pixels) {
            char pixel[] = {static_cast<char>(rgb >> 16), static_cast<char>(rgb >> 8), static_cast<char>(rgb)};
            output.write(pixel, 3);
        }
        return output ? 0 : 1;
    }
    if (argc != 1) return 64;
    if (geteuid() == 0) { std::cerr << "Recovery requires an unprivileged session\n"; return 78; }
    sigset_t blocked{}, previous{};
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGTERM); sigaddset(&blocked, SIGINT); sigaddset(&blocked, SIGHUP);
    sigprocmask(SIG_BLOCK, &blocked, &previous);
    std::signal(SIGTERM, stop); std::signal(SIGINT, stop); std::signal(SIGHUP, stop);
    Terminal terminal;
    if (!framebuffer(pixels))
        std::cerr << "TYRELL ZERO RECOVERY: use configured SSH or a known reference card\n";
    while (!stopped) sigsuspend(&previous);
    sigprocmask(SIG_SETMASK, &previous, nullptr);
    return 0;
}
