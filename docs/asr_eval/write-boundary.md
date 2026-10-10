# 「改哪个文件会改安装包」——可写边界清单（机器实测）

> 用途：判断一次改动是否会改变安装包字节 → 是否会让**已冻结的 v3.4.1 交付证据**
> （`packaging/verify/report-v341.json` + 真机 e2e 结论 + Release Note 里的 sha256/md5）当场作废。
>
> 本文件不是「按 `package.json` 推断」出来的，而是**从真实打包产物里逐条抠出来的**
> （教训 34：按心智模型写的清单只能验证心智模型）。所有数字都有可复跑的命令，见 §6。

## 1. 结论（一句话）

| 改动位置 | 会改包吗 | 说明 |
|---|---|---|
| `<repo>/scripts/**` | **不会** | 评估/运维脚本，`build.files` 与 `extraResources` 都没它 |
| `<repo>/testset/**` | **不会** | 素材与真值，不进包 |
| `<repo>/backend/tests/**` | **不会** | `extraResources` 第 1 条显式 `!tests/**` 排除 |
| `<repo>/docs/**`、根级 `*.md` | **不会** | 文档不进包 |
| `<repo>/packaging/verify/**` | **不会** | 只取 `packaging/out/backend-server` 与 `packaging/seed/whisper_models` |
| `<repo>/tests/**`（根，合成语音） | **不会** | 只取 `../backend`，不含根 `tests/` |
| `frontend/tests/**`、`frontend/scripts/**` | **不会** | 不在 `build.files` 里，也不在其他 extraResources 里 |
| `frontend/src/**` | **不会**（前提：不重新 `vite build`） | `src` 不直接进包；**只有重建 `frontend/dist/` 才改包** |
| **`frontend/electron/**`** | **会** | `build.files` 第 1 条 → asar 内 `/electron`（5 个文件） |
| **`frontend/package.json`** | **会** | `build.files` 第 2 条 → asar 内 `/package.json` |
| **`frontend/node_modules/**`（生产依赖）** | **会** | 见 §4 反直觉点① |
| **`frontend/dist/**`** | **会** | `extraResources` 第 4 条 `{from:'dist',to:'dist'}` |
| **`backend/**` 除 `tests/`、`__pycache__/`、`.pytest_cache/`、`dictionary/_backup_batches/`** | **会** | `extraResources` 第 1 条（实测 28 个文件） |
| **`packaging/out/backend-server/**`** | **会** | `extraResources` 第 2 条（PyInstaller 冻结后端） |
| **`packaging/seed/whisper_models/**`** | **会** | `extraResources` 第 3 条（内置模型种子） |
| `tingke-banlv/`（本地副本） | 不会 | gitignore 的仓内副本，与打包无关 |

## 2. 包的构成（实测）

安装包 `frontend/release/tingkebanlv-3.4.1-setup.exe` = **302,570,573 B**，
解包后 `win-unpacked/resources/` 只有六项：

```
app.asar            34,139,610 B
backend/            ← extraResources ①（../backend，filter 排除 tests/**）
backend-server/     ← extraResources ②（../packaging/out/backend-server）
dist/               ← extraResources ③…… 实为第 4 条（frontend/dist）
elevate.exe         ← electron-builder 自带
whisper_models/     ← extraResources ④（../packaging/seed/whisper_models）
```

`app.asar` 头部清单（本机解析，非估算）：

```
文件条目总数 = 3688
顶层（名: 文件数）= {"electron": 5, "node_modules": 3682, "package.json": 1}
/electron/  = main.cjs / onboarding-state.cjs / overlay-actions.cjs /
              overlay-settings.cjs / preload.cjs
node_modules 顶层包 = @remix-run dexie js-tokens loose-envify lucide-react
                     react react-dom react-router react-router-dom
                     scheduler use-sync-external-store zustand
```

`resources/backend/` = **28 个文件**：26 个 `.py`（`api/` `asr/` `audio/` `database/`
`dictionary/` `llm/` `dependencies.py` `main.py`）+ `requirements.txt` + `dictionary/basic_vocab.json`。
**没有 `tests/`**（filter `!tests/**` 生效）。

## 3. 判定规则（怎么用）

> **一次改动会不会改包 = 它是否落在 §1 表里「会」的那几行。**

`build.files` 只写了 `electron/**/*` 与 `package.json`，但**这远远不等于包的内容**——
真正决定包内容的是：

```
build.files          →  app.asar（+ 自动带上的生产依赖）
build.extraResources →  resources/ 下那四个目录
```

漏看 `extraResources` 就会得出「只改前端才影响包」的错误结论（`backend/` 也在包里）。

## 4. 三个反直觉点（都踩过）

### ① `node_modules` 进包，`package-lock.json` 不进包

`build.files` 里根本没写 `node_modules`，但 electron-builder **会自动把生产依赖塞进 asar**
（实测 3682 个条目、13 个顶层包）。
⇒ **任何 `npm install` / `npm ci` 都会改 asar → 改安装包哈希 → 让该版本已通过的 L4 与真机 e2e 证据作废。**
所以「锁文件陈旧」只能做**最小字段更正**，不能整体重生成；
只读校验用 `npm ci --dry-run`（exit=0 即表示锁与 `package.json` 同步）。
（见 `frontend/package-lock.json` 修正提交 `51c5036` 与 `LESSONS.md` 教训 39。）

### ② `backend/tests/` 不进包，但 `backend/` 里**任何其它新增 `.py`** 都进包

`filter` 只排除了 `tests/**`、`__pycache__/**`、`*.pyc`、`.pytest_cache/**`、
`dictionary/_backup_batches/**`。
⇒ 往 `backend/` 里放一个「顺手写的评估模块」= 直接改包。
**评估类代码一律放 `<repo>/scripts/`。**

### ③ 改 `frontend/src` **不会**直接改包

`src` 不在 `build.files`，也不在 `extraResources`。
只有执行 `npm run build`（`vite build`）重新生成 `frontend/dist/` 之后，包才会变。
⇒ 「改前端源码就要重打包」这句话是**不精确的**；准确说法是
「要让它生效就必须重建 `dist`，重建 `dist` 才改包」。
（这不改变结论：任何要上线的前端行为改动，都必须重打包 + 全量 L3/L4 回归。）

## 5. 对 P2（ASR 质量评估）的约束

| 要写的东西 | 落点 | 是否改包 |
|---|---|---|
| 评估脚本 / harness | `<repo>/scripts/` | 否 |
| 素材 + 真值（逐字稿、metadata） | `<repo>/testset/` | 否 |
| 评估报告产物 | `packaging/eval/`（gitignore） | 否 |
| P2 方案 / 清单文档 | `docs/asr_eval/` | 否 |
| 后端测试 | `backend/tests/` | 否 |

🔴 **禁止**：把素材或评估代码放进 `packaging/seed/whisper_models/`、`packaging/out/`、
`backend/`（非 tests）、`frontend/electron/`、`frontend/dist/`。

## 6. 复核命令（可随时重跑）

```bash
# ① 解 app.asar 清单（只读工具，放在仓库外的 安装空间/，与 _abcheck.py / _mirror_check.py 同例）
<py> "听课伴侣/安装空间/_asar_list.py" frontend/release/win-unpacked/resources/app.asar

# ② 看 extraResources 落点（有没有 tests）
find frontend/release/win-unpacked/resources/backend -type f | wc -l   # → 28
ls frontend/release/win-unpacked/resources/backend/tests              # → No such file

# ③ 全包扫可疑目录（应只剩 resources/backend-server）
find frontend/release/win-unpacked -maxdepth 4 \
  \( -name testset -o -name scripts -o -name "*.test.*" \) -print

# ④ 交付物指纹（改包即变）
sha256sum frontend/release/tingkebanlv-3.4.1-setup.exe
#   → 4a92fee21a7949ee5a6ade1376770b703fbdeaf13514d96e027187a3faf9060a
```

> `frontend/release/` 是 gitignore 的产物目录；`win-unpacked/` 由最后一次打包留下，
> 若被清理则需重新 `electron-builder` 才能复现上述清单（重打包本身就会改哈希）。
