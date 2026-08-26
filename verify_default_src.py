import json, threading, time, urllib.request
import yijianditu.server as srv

httpd, port = srv.serve()
t = threading.Thread(target=httpd.serve_forever, daemon=True)
t.start()
time.sleep(0.3)


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.loads(r.read())


cfg = get("/api/config")
print("default_source =", cfg["default_source"])
assert cfg["default_source"] == "arcgis_satellite", "默认源应为 arcgis_satellite"
src_ids = [x["id"] for x in cfg["sources"]]
print("sources =", src_ids)
assert "arcgis_satellite" in src_ids and "tianditu_satellite" in src_ids

# 触发关闭
req = urllib.request.Request(f"http://127.0.0.1:{port}/api/close", data=b"{}",
                             headers={"Content-Type": "application/json"}, method="POST")
urllib.request.urlopen(req).read()
for _ in range(40):
    if not t.is_alive():
        print("CLOSE_OK: server exited after /api/close")
        break
    time.sleep(0.1)
else:
    print("WARN: server did not exit")
httpd.shutdown()
print("DONE")
