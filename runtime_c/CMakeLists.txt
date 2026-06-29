cmake_minimum_required(VERSION 3.16)

project(smollm2_q8_0_runtime_c C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_library(gemv_q8_0_ref STATIC gemv_q8_0_ref.c)
target_compile_definitions(gemv_q8_0_ref PRIVATE GEMV_Q8_0_REF_NO_MAIN)
target_compile_options(gemv_q8_0_ref PRIVATE -Wall -Wextra -Wpedantic)

add_executable(gemv_q8_0_test gemv_q8_0_test.c)
target_link_libraries(gemv_q8_0_test PRIVATE gemv_q8_0_ref m)
target_compile_options(gemv_q8_0_test PRIVATE -Wall -Wextra -Wpedantic)
