# build/ —— electron-builder 资源目录（buildResources）

这个目录是 electron-builder 的 `directories.buildResources` **默认位置**
（工程根 = `frontend/`，因为 `electron:build` 在 `frontend/` 下执行）。
里面的图标在**构建期**被打进 exe / 安装包，**不会**进 `app.asar` ——
这也是 `main.cjs` 里 `resolveAppIconPath()` 打包后返回 `undefined` 的原因
（打包版图标由 exe 自带，运行时再传 `icon` 参数毫无意义）。

⚠️ **不要**把图标挪到 `frontend/release/`（构建产物，被 gitignore）或
`frontend/dist/`（Vite 产物，被 gitignore）。`frontend/build/` **不在**
`.gitignore` 里，图标作为源码资产正常入库。

## 当前状态：⚠️ 占位图标（等待美术资源交付）

| 文件 | 状态 |
|---|---|
| `icon.ico` | **占位版本**，由 `frontend/scripts/make-placeholder-icon.py` 生成 |
| `icon.icns` | 未做（暂不支持 macOS 分发，见设计稿 §7 待决策项 1） |
| `installerIcon.ico` / `installerHeaderIcon.ico` | 未做（NSIS 缺失时回落 `icon.ico`，见待决策项 2） |

占位图语义：深紫底（取 `global.css` 的 `--accent`）+ 两条居中字幕条。
**它是能用的合格图标，但不是终稿** —— 美术资源交付后直接覆盖 `icon.ico`
即可，代码与配置无需再改。

## 替换占位图标时的交付清单（来自 `docs/m3/icon_asset_spec.md`）

- [ ] 单个 `.ico` 多尺寸容器，包含 **16 / 24 / 32 / 48 / 64 / 128 / 256** 七档
      （可**额外**附 96 档——`icon_assets.py check` 只判「必需档缺失」，多余档不报错；
      但 **24 档不可省**，部分 DPI 档位要用，缺了 `check` 直接 FAIL）
- [ ] 256 档用 **PNG 压缩**（总体积应 < 400 KB，占位版约 100 KB 可作参照）
- [ ] **32 位 RGBA** + 背景完全透明（不透明背景在任务栏上是方块）
- [ ] 色彩空间 sRGB；源文件从 **1024×1024 母版**下采样（禁止从 256 放大）
- [ ] 安全区：内容不超出画布 90%
- [ ] **16×16 缩到实际尺寸目视确认可辨识**（人工判断，不可跳过）
- [ ] 不要放文字 / 汉字 / 细线条（16px 下必然糊掉）
- [ ] 替换后**清空 `frontend/release/`** 再重新构建，避免旧产物缓存
- [ ] 验收按设计稿 §6 走：构建无 `default Electron icon is used` 警告 +
      实装六处落点（安装向导 / 桌面快捷方式 / 开始菜单 / 任务栏 / Alt+Tab /
      控制面板卸载项）

### 制作要点（2026-10-07 补，给美术）

**从矢量母版导出 7~8 张独立 PNG**（不要把矢量直接丢进 ico 生成器拉伸），
全部 32bit RGBA / sRGB / 透明背景：

| 档位 | 要求 |
|---|---|
| 256 | 主图，PNG 压缩（核心校验项） |
| 128 / 64 / 48 | 完整保留设计细节 |
| 32 | 保留极简五官，书签等细线条**加粗** |
| 24 | 必需；同 32 的简化逻辑 |
| 16 | **删掉五官细碎线条**，只留头部轮廓 / 帽子剪影 / 主体大形；**不要细线条** |

- **小尺寸必须单独优化，禁止从 256 机械缩**——16px 由大图缩下来会糊成一团（§4.1 第 1 条）。
- **工具**：IcoFX / ImageMagick。**禁止** Windows 自带画图、普通在线 ico 网站
  （容易把 256 档存成裸 BMP）。

  ```bash
  magick icon_256.png icon_128.png icon_64.png icon_48.png icon_32.png icon_24.png icon_16.png icon.ico
  ```
- **交付前必跑预校验**（不通过退回，禁止强行放行）：

  ```bash
  python scripts/icon_assets.py check icon.ico
  ```
- 交付 `.ico` 即可，PNG 中间素材自行存档；**矢量源文件（SVG/AI）请务必留档**
  （后续改版 / 出 macOS icns 都要从它出发）。
- ⚠️ AI 生成稿注意事项：输出常为**无 alpha 的 JPG/PNG 实底图**且**自带水印**
  （如「豆包AI生成」角标）——水印必须去除；实底背景需重做成透明；
  且这类图通常只有 256~512px，**没有 1024 母版余量**，正式交付请走矢量重绘。

### 矢量源与生成脚本（2026-10-07 起，零美术路线）

`icon-source/` 是图标的**矢量源与生成器**目录：

| 文件 | 说明 |
|---|---|
| `tingke-otter-master.svg` | 256 母版，**分层多色**（6 个 Inkscape 图层：底色 / 主体渐变 / 面部 / 学士帽 / 笔记本声波 / 白描边），可逐层开关 |
| `tingke-otter-24.svg` | 24px **独立手绘**剪影（圆头 + 学士帽 + 极简笔记本色块），**不是**主矢量缩放 |
| `tingke-otter-16.svg` | 16px **独立手绘**剪影（仅头部 + 学士帽），零细节、高对比 |
| `build_otter.py` | 生成器：同一套几何参数**双端输出** SVG 与位图 |

**单一数据源约定**：几何参数集中在 `build_otter.py` 顶部常量区。改参数 → 重跑脚本，
SVG 与 PNG/ico 一起更新。**不要只手工改 SVG 而不跑脚本**（两边会漂移）。
需要精修造型时，用 Inkscape 打开 SVG 调锚点，改完把参数回填脚本再重跑。

```bash
# 依赖 Pillow + numpy —— 注意：项目 venv 里没有 PIL，用系统 python 或托管 env
python frontend/build/icon-source/build_otter.py --out <输出目录>
```

产物：`<输出目录>/png/icon_{256,128,96,64,48,32,24,16}.png` + `<输出目录>/tingke-otter.ico`
（8 档，已含 24 / 96）。**不需要 Inkscape / ImageMagick**，打包由 Pillow 完成。

> ⚠️ 生成/替换 ico 之后必须走 `docs/m3/icon_asset_spec.md` **§8.5 唯一清单**验收
> （清 `release/` → 重新打包 → `audit` → verify_install → 六处落点目检）。

## 换图后的机器校验（`scripts/icon_assets.py`）

> 📌 **完整流水线见 `docs/m3/icon_asset_spec.md` §8.5（唯一执行清单）**，
> 本节只是校验命令的速查。**注意顺序**：`audit` 必须放在清 `release/` +
> 重新打包之后 —— 放在覆盖 ico 之后会读到上一次的旧产物，**假绿**。

上面那份清单里，除了「16px 目视」和「构建警告」，**其余都能机器判**。换图后跑：

```bash
# 交付件先验（还没进仓库就能判，不通过直接打回美术）
<venv>/python.exe scripts/icon_assets.py check <美术交付目录>/icon.ico

# 覆盖 + 清缓存 + 重建后，验产物
<venv>/python.exe scripts/icon_assets.py audit
```

`audit` 会对拍三份产物：`icon.ico` 本体、`release/win-unpacked/*.exe`、
NSIS 安装包，并在装完之后把已安装 exe / 快捷方式 / 注册表 `DisplayIcon` 一起看了。

> **为什么非要验产物**：六处落点里有 4 处（桌面 / 开始菜单 / 任务栏 / 控制面板）
> **继承 exe 内嵌图标**。exe 错了它们一起错，**不报错、不崩溃** —— 只表现为
> 「有的地方是新图标、有的还是旧的」。源码层守卫（`npm test` 里的 AUMID 用例）
> 只能证明配置写对了，证明不了图标真的进了 exe。
>
> 详见 `docs/m3/icon_asset_spec.md` §8.4。

## 重新生成占位图标（一般不需要）

```bash
python frontend/scripts/make-placeholder-icon.py --preview <目录>
```
