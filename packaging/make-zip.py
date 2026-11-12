"""
免安装包打包（一次性工具）

为什么不用 tar -a：Git Bash 的 GNU tar **不支持 zip 容器**
（`-a` 只按后缀猜压缩算法，产物仍是 tar 流，解压会失败）。
本机也没有 7-Zip。用 Python 的 zipfile，参数与之前 3.8.0 的产物保持一致。

用法: python make-zip.py <源目录> <输出 zip>
"""
import os
import sys
import zipfile

src = sys.argv[1]
out = sys.argv[2]

if not os.path.isdir(src):
    raise SystemExit(f"源目录不存在: {src}")

count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
    for root, _dirs, files in os.walk(src):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, os.path.dirname(os.path.abspath(src)))
            zf.write(full, rel)
            count += 1
            if count % 200 == 0:
                print(f"  ... {count} 个文件", flush=True)

size_mb = os.path.getsize(out) / 1024 / 1024
print(f"[zip] {out}  {count} 个文件  {size_mb:.1f} MB")
