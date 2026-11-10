"""When the account or region has no model of the configured name, the known alternatives are tried."""
import http.server
import json
import threading

from app.engine import ai


def test_model_fallback(monkeypatch):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body["model"])
            if body["model"] in ("qwen3-vl-plus", "qwen-vl-plus"):
                out, code = {"error": {"message": "Model not exist.", "code": "model_not_found"}}, 404
            else:
                out, code = {"model": body["model"], "choices": [{"message": {"content": '{"ok": true}'}}]}, 200
            raw = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("QWEN_API_KEY", "k")
    monkeypatch.setenv("QWEN_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    monkeypatch.delenv("QWEN_VISION_MODEL", raising=False)
    ai._resolved.clear()
    p = ai.providers()["qwen"]
    assert p.vision_model == "qwen3-vl-plus"
    out = ai.chat(p, p.vision_model, [{"role": "user", "content": "x"}])
    assert out["model"] == "qwen3.8-flash" and seen == ["qwen3-vl-plus", "qwen3.8-flash"]
    seen.clear()
    ai.chat(p, p.vision_model, [{"role": "user", "content": "x"}])
    assert seen == ["qwen3.8-flash"]          # remembered
    srv.shutdown()
