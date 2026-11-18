from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config = str(Path(__file__).resolve().parents[1] / 'config/pipeline.yaml')
    return LaunchDescription([
        DeclareLaunchArgument('data_dir', default_value='data/payload'),
        DeclareLaunchArgument('bus_required', default_value='false'),
        Node(package='payload_storage', executable='storage_node', name='payload_storage',
             parameters=[config, {'data_dir': LaunchConfiguration('data_dir')}], output='screen'),
        Node(package='payload_processing', executable='processing_node', name='payload_processing',
             parameters=[config], output='screen'),
        Node(package='payload_camera', executable='camera_node', name='payload_camera',
             parameters=[config], output='screen'),
        Node(package='payload_modes', executable='modes_node', name='payload_modes',
             parameters=[config, {'bus_required': LaunchConfiguration('bus_required')}], output='screen'),
        Node(package='payload_health', executable='health_node', name='payload_health',
             parameters=[config, {'data_dir': LaunchConfiguration('data_dir')}], output='screen'),
    ])
