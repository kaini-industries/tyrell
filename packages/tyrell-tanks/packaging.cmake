# Included only for the native framebuffer build. This adds no service, device
# configuration, maintainer scripts, or dependency downloads.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "Tyrell Tanks device packaging requires Debian ARM64 Linux")
endif()
if(NOT TARGET tyrell-tanks)
  message(FATAL_ERROR "Create the native tyrell-tanks target before including packaging.cmake")
endif()
execute_process(COMMAND dpkg --print-architecture
  OUTPUT_VARIABLE TANKS_DEBIAN_ARCH OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
if(NOT TANKS_DEBIAN_ARCH STREQUAL "arm64")
  message(FATAL_ERROR "Device package must be built on Debian ARM64; got ${TANKS_DEBIAN_ARCH}")
endif()
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
  set(CMAKE_INSTALL_PREFIX "/usr" CACHE PATH "Install prefix" FORCE)
endif()
set(TANKS_PACKAGE_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}")
foreach(TANKS_LICENSE "${ENET_SOURCE_DIR}/LICENSE" "${RAYLIB_SOURCE_DIR}/LICENSE")
  if(NOT EXISTS "${TANKS_LICENSE}")
    message(FATAL_ERROR "Required third-party license is missing: ${TANKS_LICENSE}")
  endif()
endforeach()
find_package(Python3 3.12 REQUIRED COMPONENTS Interpreter)
set(TANKS_NOTICES "${CMAKE_CURRENT_BINARY_DIR}/raylib-third-party-notices.txt")
execute_process(COMMAND "${Python3_EXECUTABLE}"
  "${TANKS_PACKAGE_SOURCE_DIR}/assets/make_notices.py" "${RAYLIB_SOURCE_DIR}" "${TANKS_NOTICES}"
  COMMAND_ERROR_IS_FATAL ANY)
file(GLOB TANKS_NOTICE_SOURCES CONFIGURE_DEPENDS
  "${RAYLIB_SOURCE_DIR}/src/external/*.h" "${RAYLIB_SOURCE_DIR}/src/external/*.c")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${TANKS_PACKAGE_SOURCE_DIR}/assets/make_notices.py"
  "${TANKS_PACKAGE_SOURCE_DIR}/../tyrell-game/tools/make_notices.py"
  ${TANKS_NOTICE_SOURCES})

install(TARGETS tyrell-tanks RUNTIME DESTINATION bin)
install(FILES "${TANKS_PACKAGE_SOURCE_DIR}/assets/tyrell-tanks.desktop"
  DESTINATION share/APPLaunch/applications)
install(FILES "${TANKS_PACKAGE_SOURCE_DIR}/assets/tyrell-tanks-standard.desktop"
  DESTINATION share/applications RENAME tyrell-tanks.desktop)
install(FILES "${TANKS_PACKAGE_SOURCE_DIR}/assets/tyrell-tanks.png"
  DESTINATION share/APPLaunch/share/images)
install(FILES "${TANKS_PACKAGE_SOURCE_DIR}/assets/tyrell-tanks.png"
  DESTINATION share/icons/hicolor/80x80/apps)
install(FILES "${TANKS_PACKAGE_SOURCE_DIR}/LICENSE" DESTINATION share/doc/tyrell-tanks)
install(FILES "${ENET_SOURCE_DIR}/LICENSE"
  DESTINATION share/doc/tyrell-tanks RENAME enet-LICENSE.txt)
install(FILES "${RAYLIB_SOURCE_DIR}/LICENSE"
  DESTINATION share/doc/tyrell-tanks RENAME raylib-LICENSE.txt)
install(FILES "${TANKS_NOTICES}" DESTINATION share/doc/tyrell-tanks)

set(CPACK_GENERATOR DEB)
set(CPACK_PACKAGE_NAME tyrell-tanks)
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_CONTACT "Tyrell contributors <tyrell@localhost>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Sandline Tanks for the M5Stack CardputerZero")
set(CPACK_PACKAGE_DESCRIPTION "Small native 3D tank skirmish with a driving range and up to four players over LAN. Uses the built-in keyboard and LCD with software rendering. Includes APPLaunch integration.")
set(CPACK_PACKAGING_INSTALL_PREFIX /usr)
set(CPACK_DEBIAN_PACKAGE_NAME tyrell-tanks)
set(CPACK_DEBIAN_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_DEBIAN_PACKAGE_RELEASE 1)
set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE arm64)
set(CPACK_DEBIAN_PACKAGE_SECTION games)
set(CPACK_DEBIAN_PACKAGE_PRIORITY optional)
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
set(CPACK_DEBIAN_PACKAGE_CONTROL_STRICT_PERMISSION TRUE)
set(CPACK_PACKAGE_FILE_NAME "tyrell-tanks_${PROJECT_VERSION}-1_arm64")
include(CPack)
