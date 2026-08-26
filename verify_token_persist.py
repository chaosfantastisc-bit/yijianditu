import os, importlib
import yijianditu.config as c

FP = c.TIANDITU_TOKEN_FILE
# 清理可能存在的旧文件，保证测试纯净
if os.path.isfile(FP):
    os.remove(FP)

# 1) 设一个新 Key，应落盘
c.set_tianditu_token("MY_OWN_KEY_2026")
assert os.path.isfile(FP), "Key 未写入文件"
with open(FP, "r", encoding="utf-8") as f:
    assert f.read().strip() == "MY_OWN_KEY_2026", "文件内容不符"
print("[1] 写入落盘 OK ->", c.current_tianditu_token())

# 2) 空 Key 不应写入/覆盖
c.set_tianditu_token("")
with open(FP, "r", encoding="utf-8") as f:
    assert f.read().strip() == "MY_OWN_KEY_2026", "空 Key 不应清空文件"
print("[2] 空 Key 不覆盖 OK")

# 3) 模拟重启：重新加载模块，应从文件回填
importlib.reload(c)
assert c.current_tianditu_token() == "MY_OWN_KEY_2026", "重启后未记住 Key: " + c.current_tianditu_token()
print("[3] 重启自动回填 OK ->", c.current_tianditu_token())

# 4) /api/config 回显也应是记住的 Key
import yijianditu.server as srv  # 触发模块；实际回显走 current_tianditu_token
assert srv.current_tianditu_token() == "MY_OWN_KEY_2026"
print("[4] config 回显 OK")

# 清理测试文件
os.remove(FP)
print("ALL_OK")
