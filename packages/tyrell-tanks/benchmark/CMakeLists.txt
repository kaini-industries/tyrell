# Independent software raylib build: the desktop game may use another backend.
if(NOT EXISTS "${RAYLIB_SOURCE_DIR}/src/rcore.c")
    message(FATAL_ERROR "Display benchmark needs RAYLIB_SOURCE_DIR pointing to raylib 6.2")
endif()
find_package(Threads REQUIRED)
add_library(tanks_bench_raylib STATIC "${RAYLIB_SOURCE_DIR}/src/rcore.c")
target_include_directories(tanks_bench_raylib PUBLIC "${RAYLIB_SOURCE_DIR}/src")
target_compile_definitions(tanks_bench_raylib PRIVATE
    PLATFORM_MEMORY GRAPHICS_API_OPENGL_SOFTWARE
    SUPPORT_CUSTOM_FRAME_CONTROL=1 SUPPORT_SCREEN_CAPTURE=0
    SUPPORT_MODULE_RSHAPES=0 SUPPORT_MODULE_RTEXTURES=0 SUPPORT_MODULE_RTEXT=0
    SUPPORT_MODULE_RMODELS=0 SUPPORT_MODULE_RAUDIO=0 SUPPORT_COMPRESSION_API=0
    SUPPORT_CLIPBOARD_IMAGE=0 SUPPORT_AUTOMATION_EVENTS=0
    RLSW_FRAMEBUFFER_OUTPUT_BGRA=0)
target_link_libraries(tanks_bench_raylib PUBLIC Threads::Threads ${CMAKE_DL_LIBS})
if(UNIX)
    target_link_libraries(tanks_bench_raylib PUBLIC m)
endif()
add_executable(tanks-display-bench-rlsw benchmark.cpp)
target_compile_features(tanks-display-bench-rlsw PRIVATE cxx_std_17)
target_compile_definitions(tanks-display-bench-rlsw PRIVATE
    TANKS_BENCH_RLSW=1 GRAPHICS_API_OPENGL_SOFTWARE)
target_link_libraries(tanks-display-bench-rlsw PRIVATE tanks_bench_raylib)

if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter QUIET)
    if(Python3_Interpreter_FOUND)
        add_test(NAME tanks_display_benchmark_smoke
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/verify_benchmark.py"
                --binary "$<TARGET_FILE:tanks-display-bench-rlsw>")
        add_test(NAME tanks_display_benchmark_invalid_cli
            COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/verify_benchmark.py"
                --binary "$<TARGET_FILE:tanks-display-bench-rlsw>" --invalid)
        set_tests_properties(tanks_display_benchmark_smoke tanks_display_benchmark_invalid_cli
            PROPERTIES TIMEOUT 15)
    endif()
endif()

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    find_path(TANKS_EGL_INCLUDE_DIR EGL/egl.h)
    find_library(TANKS_EGL_LIBRARY EGL)
    find_library(TANKS_GLES_LIBRARY GLESv2)
    if(TANKS_EGL_INCLUDE_DIR AND TANKS_EGL_LIBRARY AND TANKS_GLES_LIBRARY)
        add_executable(tanks-display-bench-gles benchmark.cpp)
        target_compile_features(tanks-display-bench-gles PRIVATE cxx_std_17)
        target_compile_definitions(tanks-display-bench-gles PRIVATE TANKS_BENCH_GLES=1)
        target_include_directories(tanks-display-bench-gles PRIVATE "${TANKS_EGL_INCLUDE_DIR}")
        target_link_libraries(tanks-display-bench-gles PRIVATE
            "${TANKS_EGL_LIBRARY}" "${TANKS_GLES_LIBRARY}" Threads::Threads)
    else()
        message(STATUS "EGL/GLES2 headers or libraries absent: GPU benchmark omitted")
    endif()
endif()
