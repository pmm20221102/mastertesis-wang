# Docker on a remote workstation

The Compose setup runs the Hospital demo, Nav2 and RViz2 on the workstation.
The client needs SSH and a browser. The default profile is Human-reviewed RAG
(v5), containing the 78 manually supplemented object labels.

Install Docker Engine, the Compose plugin and NVIDIA Container Toolkit on the
workstation. Verify the GPU with a CUDA container before starting.

From a clone of thesis-final on the workstation:

    docker compose build
    docker compose up -d ollama
    docker compose exec ollama ollama pull gemma4:e2b-it-qat
    docker compose up -d demo
    docker compose logs -f demo

On the client, keep an SSH tunnel open:

    ssh -L 8765:127.0.0.1:8765 -L 6080:127.0.0.1:6080 USER@WORKSTATION

For KIT, connect the KIT VPN first and use your own workstation account in
the SSH command. Leave the tunnel terminal open while using the demo.

Open http://127.0.0.1:8765 for planning and
http://127.0.0.1:6080/vnc.html for the RViz2 desktop. Use the demo's navigation
button after planning. The selected Room labels, maps and navigation results
remain visible on the remote desktop.

To stop services, run docker compose down. Named volumes retain downloaded
models and session outputs. To inspect GPU use, run docker compose exec ollama ollama ps after a
planning request. Session JSON and Nav2 logs are in the cache volume under
/root/.cache/hospital-nav2-ui.

A replacement RAG needs its matching maps and
run profile, as described in the demo README.

The image provides the bundled demo and tools for RAG and map generation.
Khronos reconstruction has its separate build instructions in khronos/.
