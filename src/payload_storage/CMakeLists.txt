cmake_minimum_required(VERSION 3.16)
project(payload_storage)
find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
# Install the canonical host-tested library rather than duplicating durability logic.
ament_python_install_package(foundations PACKAGE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../foundations)
ament_python_install_package(interfaces PACKAGE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../../interfaces)
install(PROGRAMS scripts/storage_node.py DESTINATION lib/${PROJECT_NAME} RENAME storage_node)
ament_package()
