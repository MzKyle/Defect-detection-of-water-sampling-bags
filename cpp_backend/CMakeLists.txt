cmake_minimum_required(VERSION 3.16)

project(waterbag_cpp_backend LANGUAGES CXX)

option(WATERBAG_ENABLE_ONNXRUNTIME "Enable optional ONNX Runtime CUDA detector" OFF)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Threads REQUIRED)
find_path(HIKVISION_MVS_INCLUDE_DIR
    NAMES MvCameraControl.h
    PATHS /opt/MVS/include
)
find_library(HIKVISION_MVS_LIBRARY
    NAMES MvCameraControl
    PATHS /opt/MVS/lib/64 /opt/MVS/lib
)

if (NOT HIKVISION_MVS_INCLUDE_DIR OR NOT HIKVISION_MVS_LIBRARY)
    message(FATAL_ERROR "camera_driver requires Hikvision MVS SDK. Expected MvCameraControl.h and libMvCameraControl under /opt/MVS.")
endif()

add_library(detect_orchestrator_common
    detect_orchestrator/src/config.cpp
    detect_orchestrator/src/hardware_clock.cpp
    detect_orchestrator/src/logger.cpp
    detect_orchestrator/src/schemas.cpp
    detect_orchestrator/src/storage.cpp
)

add_library(camera_driver
    camera_driver/src/burst_capture.cpp
    camera_driver/src/hikvision_mvs_burst_capture.cpp
)

add_library(mock_camera_driver
    mock_camera_driver/src/mock_burst_capture.cpp
)

add_library(PLC_driver
    PLC_driver/src/plc.cpp
    PLC_driver/src/plc_controller.cpp
)

add_library(detect_orchestrator
    detect_orchestrator/src/bag_runtime.cpp
    detect_orchestrator/src/correlator.cpp
    detect_orchestrator/src/detector.cpp
    detect_orchestrator/src/pipeline.cpp
    detect_orchestrator/src/runtime.cpp
)

foreach(target detect_orchestrator_common camera_driver PLC_driver detect_orchestrator)
    target_include_directories(${target}
        PUBLIC
            ${CMAKE_CURRENT_SOURCE_DIR}/camera_driver/include
            ${CMAKE_CURRENT_SOURCE_DIR}/PLC_driver/include
            ${CMAKE_CURRENT_SOURCE_DIR}/detect_orchestrator/include
    )
endforeach()

target_include_directories(mock_camera_driver
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/mock_camera_driver/include
        ${CMAKE_CURRENT_SOURCE_DIR}/camera_driver/include
        ${CMAKE_CURRENT_SOURCE_DIR}/detect_orchestrator/include
)

target_link_libraries(camera_driver
    PUBLIC
        detect_orchestrator_common
    PRIVATE
        ${HIKVISION_MVS_LIBRARY}
        Threads::Threads
)
target_include_directories(camera_driver PRIVATE ${HIKVISION_MVS_INCLUDE_DIR})
get_filename_component(HIKVISION_MVS_LIBRARY_DIR "${HIKVISION_MVS_LIBRARY}" DIRECTORY)
set_target_properties(camera_driver PROPERTIES BUILD_RPATH "${HIKVISION_MVS_LIBRARY_DIR}")

target_link_libraries(mock_camera_driver
    PUBLIC
        camera_driver
)

target_link_libraries(PLC_driver
    PUBLIC
        detect_orchestrator_common
        camera_driver
)

target_link_libraries(detect_orchestrator
    PUBLIC
        detect_orchestrator_common
        camera_driver
        PLC_driver
)

add_library(waterbag_cpp INTERFACE)
target_link_libraries(waterbag_cpp
    INTERFACE
        detect_orchestrator
)

if (CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    foreach(target detect_orchestrator_common camera_driver mock_camera_driver PLC_driver detect_orchestrator)
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    endforeach()
endif()

if (WATERBAG_ENABLE_ONNXRUNTIME)
    find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)
    find_path(ONNXRUNTIME_INCLUDE_DIR onnxruntime_cxx_api.h)
    find_library(ONNXRUNTIME_LIBRARY NAMES onnxruntime)

    if (NOT ONNXRUNTIME_INCLUDE_DIR OR NOT ONNXRUNTIME_LIBRARY)
        message(FATAL_ERROR "WATERBAG_ENABLE_ONNXRUNTIME requires ONNX Runtime headers and library")
    endif()

    target_compile_definitions(detect_orchestrator PUBLIC WATERBAG_ENABLE_ONNXRUNTIME=1)
    target_include_directories(detect_orchestrator PRIVATE ${ONNXRUNTIME_INCLUDE_DIR} ${OpenCV_INCLUDE_DIRS})
    target_link_libraries(detect_orchestrator PRIVATE ${ONNXRUNTIME_LIBRARY} ${OpenCV_LIBS})
endif()

add_executable(waterbag_cpp_demo detect_orchestrator/src/main.cpp)
target_link_libraries(waterbag_cpp_demo PRIVATE waterbag_cpp mock_camera_driver)

add_executable(waterbag_cpp_service detect_orchestrator/src/service_main.cpp)
target_link_libraries(waterbag_cpp_service PRIVATE waterbag_cpp mock_camera_driver)

enable_testing()
add_executable(waterbag_cpp_tests detect_orchestrator/tests/backend_tests.cpp)
target_link_libraries(waterbag_cpp_tests PRIVATE waterbag_cpp mock_camera_driver)
add_test(NAME waterbag_cpp_tests COMMAND waterbag_cpp_tests)
set_tests_properties(waterbag_cpp_tests PROPERTIES WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/..)
