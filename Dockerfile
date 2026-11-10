FROM osrf/ros:jazzy-desktop@sha256:2f520187e84304fffb60d19c9a7d8e2e79d563a6e579a090427e5994eb1b8c45
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:0 LIBGL_ALWAYS_SOFTWARE=1
WORKDIR /opt/thesis
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-pip python3-yaml \
    python3-colcon-common-extensions python3-rosdep build-essential \
    ros-jazzy-rmw-cyclonedds-cpp ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-tf-transformations \
    xvfb x11vnc novnc websockify openbox curl \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN rosdep update && \
    rosdep install --from-paths khronos-nav2-bridge/khronos_nav2_bridge --ignore-src -r -y && \
    source /opt/ros/jazzy/setup.bash && \
    cd khronos-nav2-bridge && \
    colcon build --packages-select khronos_nav2_bridge --symlink-install
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r llm-rag/requirements.txt
RUN chmod +x docker/entrypoint.sh
ENTRYPOINT ["/opt/thesis/docker/entrypoint.sh"]
