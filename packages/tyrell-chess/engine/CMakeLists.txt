add_library(chess_engine STATIC
    chess_board.cpp chess_rules.cpp chess_ai.cpp chess_clock.cpp chess960.cpp
    chess_zobrist.cpp chess_opening_book.cpp puzzle_data.cpp
    chess_storage_codec.cpp game_records.cpp profile_storage_codec.cpp cursor_navigation.cpp)
target_include_directories(chess_engine PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(chess_engine PUBLIC cxx_std_17)
target_compile_options(chess_engine PRIVATE -Wall -Wextra -Wpedantic)

if(BUILD_TESTING)
  add_executable(chess-engine-test tests/engine_test.cpp)
  target_link_libraries(chess-engine-test PRIVATE chess_engine)
  add_test(NAME chess-engine COMMAND chess-engine-test)
  set_tests_properties(chess-engine PROPERTIES TIMEOUT 30)
endif()
