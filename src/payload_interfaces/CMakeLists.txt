cmake_minimum_required(VERSION 3.16)
project(payload_interfaces)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(action_msgs REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME}
  msg/ImageMetadata.msg msg/Capture.msg msg/CompressedCapture.msg msg/FileReady.msg
  srv/Capture.srv
  msg/Authority.msg msg/CameraStatus.msg msg/StageEvent.msg msg/NodeHealth.msg msg/Housekeeping.msg
  srv/ApplyControl.srv srv/SetMode.srv srv/SpacecraftControl.srv
  srv/CatalogueCommand.srv
  action/CaptureBurst.action
  DEPENDENCIES sensor_msgs builtin_interfaces action_msgs)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
