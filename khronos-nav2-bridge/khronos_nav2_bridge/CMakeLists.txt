cmake_minimum_required(VERSION 3.22.1)
project(khronos_nav2_bridge)
find_package(ament_cmake REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(PACKAGE_SHARE_DIRS launch config urdf vendor rviz)
if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/generated)
  list(APPEND PACKAGE_SHARE_DIRS generated)
endif()
install(DIRECTORY ${PACKAGE_SHARE_DIRS} DESTINATION share/${PROJECT_NAME})
if(IS_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../hospital_nav2_maps)
  install(DIRECTORY ../hospital_nav2_maps DESTINATION share/${PROJECT_NAME})
endif()
install(PROGRAMS scripts/kinematic_pose_source.py scripts/mesh_to_nav2_map.py scripts/navigation_grid_geometry.py scripts/prepare_region_waypoint_missions.py scripts/generate_region_approach_trials.py scripts/plan_candidate_task_chain.py scripts/prepare_multifloor_missions.py scripts/run_task_chain_nav2.py scripts/floor_transition_core.py scripts/candidate_route_planner.py scripts/floor_mosaic_core.py scripts/multifloor_status.py scripts/hospital_floor_mosaic.py DESTINATION lib/${PROJECT_NAME})
install(FILES README_HOSPITAL.md DESTINATION share/${PROJECT_NAME} RENAME README.md)
if(BUILD_TESTING)
 find_package(ament_cmake_pytest REQUIRED)
 set(HOSPITAL_CONTRACT_TESTS
   test/test_candidate_route_planner.py
   test/test_floor_mosaic_core.py
   test/test_floor_transition_core.py
   test/test_fuse_places_into_occupancy.py
   test/test_hospital_default_map_bundle.py
   test/test_hospital_manual_rviz_contract.py
   test/test_hospital_multifloor_rviz_contract.py
   test/test_multifloor_status_contract.py
   test/test_nav2_plan_only.py
   test/test_nav2_point_robot_profile.py
   test/test_nav2_rviz_profile.py
   test/test_plan_candidate_task_chain.py
   test/test_prepare_instruction_mission.py
   test/test_prepare_multifloor_missions.py
   test/test_run_task_chain_nav2.py
 )
 foreach(TEST_FILE IN LISTS HOSPITAL_CONTRACT_TESTS)
   get_filename_component(TEST_NAME ${TEST_FILE} NAME_WE)
   ament_add_pytest_test(
     ${TEST_NAME}
     ${CMAKE_CURRENT_SOURCE_DIR}/${TEST_FILE}
     WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../..
   )
 endforeach()
 set(HOSPITAL_STATIC_CONTRACT_TESTS
   test/test_kinematic_pose_source.py
   test/test_prepare_region_waypoint_missions.py
   test/test_static_map_converter.py
 )
 foreach(TEST_FILE IN LISTS HOSPITAL_STATIC_CONTRACT_TESTS)
   get_filename_component(TEST_NAME ${TEST_FILE} NAME_WE)
   add_test(
     NAME ${TEST_NAME}
     COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${TEST_FILE}
   )
   set_tests_properties(
     ${TEST_NAME}
     PROPERTIES WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../..
   )
 endforeach()
endif()
ament_package()
