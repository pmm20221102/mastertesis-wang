"""Isolated recorded-state/fixture preview. Never imports the live controller."""
import argparse
import ast
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


def build_preview_html(state):
    root = Path(__file__).resolve().parent
    asset = root / "web" / "index.html"
    if asset.is_file():
        html = asset.read_text(encoding="utf-8")
    else:
        tree = ast.parse((root / "navigation_ui.py").read_text(encoding="utf-8"))
        html = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                        and t.id == "HTML" for t in node.targets))
    state = deepcopy(state)
    payload = json.dumps(state, ensure_ascii=True).replace("<", "\\u003c")
    shim = r'''<script>
const previewState = __PAYLOAD__;
window.fetch = async function(url, options={}) {
  const path = new URL(url, location.href).pathname;
  let ok=true, value={status:"accepted"};
  if(path==="/api/state") value=previewState;
  else if(path==="/api/route") value=previewState.route_snapshot||{segments:[]};
  else if(path==="/api/stop") {
    previewState.stage_outputs={};previewState.status="stopped";
    previewState.stage_message="Fixture stopped; route and summary retained. No navigation was run.";
  } else if(path==="/api/clear") {
    Object.assign(previewState,{status:"idle",stage_outputs:{},stage_status:{},final_summary:{},route_snapshot:null,result:null,demo_result:null,error:null,events:[],stage_message:"Fixture display cleared."});
  } else {ok=false;value={error:"Fixture preview: live operations are disabled; no navigation or model calls."};}
  return {ok,status:ok?200:405,json:async()=>JSON.parse(JSON.stringify(value))};
};
</script>'''.replace("__PAYLOAD__", payload)
    html = html.replace("<script>", shim + "<script>", 1)
    return html.replace("<body>", '<body><div style="padding:12px;background:#fff4cf;text-align:center;font:16px system-ui">Fixture preview — no navigation, ROS, model or database calls. Geometry comes from the supplied snapshot.</div>', 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    html = build_preview_html(json.loads(args.state.read_text(encoding="utf-8")))
    if args.export:
        args.export.write_text(html, encoding="utf-8")
        return
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            self.send_error(405, "Fixture preview has no live API")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Fixture preview: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
