#!/usr/bin/env python3
"""Recording proxy for LegacyLift (OpenAI-compatible APIs such as Ollama or mlx_lm.server).

Sits between the terminal and the model server and appends every chat
completion to a JSONL file: the exact request (system prompt, messages,
tool schemas) plus the reconstructed answer (text, tool calls, token usage).
Used to measure prompt sizes and to collect tool-use trajectories for
fine-tuning.

Usage: legacylift_proxy.py --listen 11500 --upstream http://localhost:11434 --out trayectorias.jsonl
Then point the provider base_url at http://localhost:11500/v1/
"""
import argparse
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()


def reconstruct(chunks):
    """Rebuild text and tool calls from OpenAI streaming chunks."""
    text, calls, usage = [], {}, None
    for c in chunks:
        if c.get("usage"):
            usage = c["usage"]
        for ch in c.get("choices", []):
            d = ch.get("delta") or ch.get("message") or {}
            if d.get("content"):
                text.append(d["content"])
            for tc in d.get("tool_calls") or []:
                i = tc.get("index", 0)
                cur = calls.setdefault(i, {"id": tc.get("id"), "name": "", "arguments": ""})
                fn = tc.get("function") or {}
                cur["name"] += fn.get("name") or ""
                cur["arguments"] += fn.get("arguments") or ""
                if tc.get("id"):
                    cur["id"] = tc["id"]
    return "".join(text), [calls[k] for k in sorted(calls)], usage


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _forward(self, method):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
        req = urllib.request.Request(self.server.upstream + self.path, data=body, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "accept-encoding"):
                req.add_header(k, v)
        start = time.time()
        chunks, raw = [], b""
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            resp = e
        self.send_response(resp.status if hasattr(resp, "status") else resp.code)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "content-length", "connection"):
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            part = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not part:
                break
            raw += part
            self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        if not (body and self.path.rstrip("/").endswith("chat/completions")):
            return
        text = raw.decode("utf-8", "replace")
        if "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    try:
                        chunks.append(json.loads(line[5:]))
                    except json.JSONDecodeError:
                        pass
        else:
            try:
                chunks.append(json.loads(text))
            except json.JSONDecodeError:
                pass
        answer, calls, usage = reconstruct(chunks)
        request = json.loads(body)
        rec = {
            "time": start, "seconds": round(time.time() - start, 2),
            "model": request.get("model"), "messages": request.get("messages"),
            "tools": request.get("tools"), "answer": answer, "tool_calls": calls, "usage": usage,
            "chars": {
                "system": sum(len(m.get("content") or "") for m in request.get("messages", []) if m.get("role") == "system"),
                "tools": len(json.dumps(request.get("tools") or [])),
                "messages": len(json.dumps(request.get("messages") or [])),
            },
        }
        with LOCK, open(self.server.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=11500)
    ap.add_argument("--upstream", default="http://localhost:11434")
    ap.add_argument("--out", default="trayectorias.jsonl")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.listen), Handler)
    srv.upstream, srv.out = a.upstream.rstrip("/"), a.out
    print(f"proxy :{a.listen} -> {srv.upstream}, registrando en {a.out}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
