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
- [ ] 256 档用 **PNG 压缩**（总体积应 < 400 KB，占位版约 100 KB 可作参照）
- [ ] **32 位 RGBA** + 背景完全透明（不透明背景在任务栏上是方块）
- [ ] 色彩空间 sRGB；源文件从 1024×1024 母版下采样（禁止从 256 放大）
- [ ] 安全区：内容不超出画布 90%
- [ ] **16×16 缩到实际尺寸目视确认可辨识**（人工判断，不可跳过）
- [ ] 不要放文字 / 汉字 / 细线条（16px 下必然糊掉）
- [ ] 替换后**清空 `frontend/release/`** 再重新构建，避免旧产物缓存
- [ ] 验收按设计稿 §6 走：构建无 `default Electron icon is used` 警告 +
      实装六处落点（安装向导 / 桌面快捷方式 / 开始菜单 / 任务栏 / Alt+Tab /
      控制面板卸载项）

## 重新生成占位图标（一般不需要）

```bash
python frontend/scripts/make-placeholder-icon.py --preview <目录>
```
