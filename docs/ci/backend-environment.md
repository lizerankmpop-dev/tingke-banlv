# 后端 CI 依赖快照

## 来源

- 环境：华为云 CodeArts Build「构建与测试」
- 构建编号：**20261108.2**
- 该环境测试结果：**512 passed / 1 skipped**
- 唯一跳过项：无可用音频设备的设备检查

## 快照文件

`ci/backend/requirements-linux-py311.lock`

| 项 | 值 |
|---|---|
| 字节数 | 2017 |
| 行数 | 105 |
| SHA-256 | `c7fa61338a14f0a437e355b6b297e62a948e8eea0d993ad25deeece7f60835d3` |

## 适用范围

- 仅针对 **Linux x86_64 + Python 3.11** 的 CI 环境。
- **不替换** Windows 开发环境的 `backend/requirements.txt`；后者仍是开发环境的依赖来源。
- 快照为精确版本列表（`name==version`），包含 GPU 相关依赖（`cuda-*`、`nvidia-*`、`torch`、`triton` 等）。
- 本次**完整保留**全部依赖，不升级、不删减。

## 使用纪律

- 本文件是已验证环境的事实记录，不是待求解的依赖声明。
- 如需更新，必须由新的 CodeArts 构建产出新快照并单独评审，不得在 CI 中重排、重写或就地升级。
- 文件按原始字节冻结；`.gitattributes` 对该精确路径设置 `-text`，防止检出时做换行转换。
- 使用该快照安装依赖时，仍应遵守 CI 的既有步骤，不在本文件内追加索引地址或凭据。