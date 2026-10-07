# M3 · 应用图标资源规范（Icon Asset Spec）

> 状态：**加载链路已实现（2026-10-07）**；`icon.ico` 当前为**占位版本**（生成器
> `frontend/scripts/make-placeholder-icon.py`，语义＝深紫底 + 字幕条，符合 §3/§4
> 全部格式要求），正式美术资源交付后直接覆盖 `frontend/build/icon.ico` 即可。
> **换图工具链已就位（2026-10-07）**：产物层校验 `scripts/icon_assets.py`（§8.4）
> + 换图一条命令（§6.1.1），美术资源到位后按 §8.3 执行即可。
> 交付清单与验收方式见 `frontend/build/README.md` 与本文 §六。
> 目标版本：M3
> 前置依赖：需要先产出美术资源（见 §4）
> 相关文档：[`onboarding_wizard.md`](./onboarding_wizard.md)、[`backlog_b1_b2_b3.md`](./backlog_b1_b2_b3.md)

---

## 一、现状（已核实，2026-10-06）

| 核查项 | 实际结果 |
|---|---|
| 全仓库 `.ico` 文件 | **0 个** |
| 全仓库 `.icns` 文件 | **0 个** |
| `frontend/public/` 目录 | 不存在 |
| `frontend/src/assets/` 目录 | 不存在 |
| `frontend/package.json` → `build.win.icon` | **字段不存在** |
| `frontend/package.json` → `build.icon` | **字段不存在** |
| `main.cjs` 中 `icon` 相关引用 | **0 处**（`mainWindow`、`overlayWindow` 均未传 `icon`） |
| `app.setAppUserModelId()` | **未调用** |

**结论：当前安装包使用的是 Electron 默认图标。** 用户装完之后：

- 桌面快捷方式 → Electron 默认图标
- 开始菜单项 → Electron 默认图标
- 任务栏 / Alt+Tab → Electron 默认图标
- 控制面板「程序和功能」卸载项 → Electron 默认图标
- 安装向导（NSIS）界面 → Electron 默认图标

对于一个已经发到 v3.4.0、带完整安装包的应用，这是**发布级缺陷**，不是「美化项」——
用户通过图标识别应用，默认 Electron 图标会直接削弱「这是个正经软件」的第一印象。

---

## 二、需要覆盖的图标落点清单

Windows 下一个应用会被**六处**独立读取图标，任何一处遗漏都会出现「有的地方是新图标、
有的地方还是默认图标」的不一致：

| # | 落点 | 由谁决定 | 现状 |
|---|---|---|---|
| 1 | 应用主程序 `.exe` 内嵌图标 | electron-builder（`win.icon`） | ❌ 默认 |
| 2 | 桌面快捷方式 | 继承 `.exe` 图标 | ❌ 默认 |
| 3 | 开始菜单项 | 继承 `.exe` 图标，名称取 `nsis.shortcutName` = 「听课伴侣」 | ❌ 默认 |
| 4 | 任务栏 / Alt+Tab | `.exe` 图标 **+** `app.setAppUserModelId` 决定分组 | ❌ 默认 + **未设置 AUMID** |
| 5 | 安装向导（NSIS）界面与安装包本体 | `nsis.installerIcon` / `installerHeaderIcon` | ❌ 默认 |
| 6 | 控制面板卸载项 | `nsis.uninstallerIcon`（缺失时回落应用图标） | ❌ 默认 |

> **备注（第 4 项的坑）**：Windows 任务栏靠 **AppUserModelID（AUMID）** 做窗口分组与
> 图标归属。electron-builder 会把 `appId`（当前为 `com.tingkebanlv.app`）写进快捷方式，
> 但**如果应用代码里没有调用 `app.setAppUserModelId('com.tingkebanlv.app')`**，
> 运行时的进程 AUMID 与快捷方式的 AUMID 不一致，会出现：
>
> - 任务栏出现**两个**图标（一个钉住的、一个运行中的）
> - 从钉住的图标启动，任务栏却在旁边新开一个
> - 未来做 Windows 原生通知时，通知来源显示错误
>
> 这一项必须在 M3 与图标一起修，否则会变成「换了图标但任务栏还是不对劲」的疑难杂症。

---

## 三、多尺寸清单

Windows ICO 是**多尺寸容器**：单个 `.ico` 文件内部打包多张不同像素的位图，
系统按场景（16px 列表、32px 任务栏、256px 大图标视图）自动挑选最合适的一张。

### 3.1 必须包含的尺寸

| 尺寸 | 使用场景 | 是否必需 |
|---|---|---|
| **16×16** | 文件列表「小图标」视图、任务栏小图标、菜单项 | ✅ 必需 |
| **24×24** | 部分 DPI 缩放下的列表视图 | ✅ 必需 |
| **32×32** | 任务栏默认、Alt+Tab、桌面「中等图标」 | ✅ 必需 |
| **48×48** | 桌面默认图标、控制面板卸载项 | ✅ 必需 |
| **64×64** | 部分缩放档位 | ✅ 建议 |
| **128×128** | 「大图标」视图 | ✅ 建议 |
| **256×256** | 「超大图标」视图、安装向导、`installerIcon` | ✅ **必需** |

> **不要只提供一个 256×256 让系统自动缩。** 系统对小尺寸的缩放算法质量很差，
> 16×16 由 256 直接缩下来会糊成一团。**16 / 24 / 32 三档必须由设计师手工调整
> 像素级对齐**，而不是从大图机械缩放。

### 3.2 macOS（`dmg` 目标）

`frontend/package.json` 里已配置 `mac` 目标，因此需要一份 `.icns`：

| 文件 | 说明 |
|---|---|
| `icon.icns` | 需包含 16/32/64/128/256/512/1024（含 `@2x` 变体） |

> macOS 图标规范与 Windows 差异很大：macOS 需要**圆角矩形 + 四周留白**（内容占画布约 80%），
> 系统**不会**自动裁切。**不能**直接把 Windows 的方形图标改后缀当 icns 用。
> 若 M3 不打算正式支持 macOS 分发，可**先不做** icns，但要在 `mac` 配置上留注释说明。

---

## 四、格式、色深与透明通道要求

| 项目 | 要求 | 原因 |
|---|---|---|
| 格式 | `.ico`（Windows）/ `.icns`（macOS） | 系统要求 |
| 色深 | **32 位 RGBA**（每通道 8 位 + Alpha） | 低于此会丢失半透明边缘，出现锯齿白边 |
| 透明通道 | **必须有**，背景完全透明 | 任务栏会按主题（浅色/深色）铺底；不透明背景会出现难看的方块 |
| 256×256 编码 | 使用 **PNG 压缩**（Vista+ 标准） | 未压缩的 256 位图会让 ico 体积暴涨到 MB 级 |
| 色彩空间 | **sRGB** | 广色域（Display P3）在其他设备上会偏色 |
| 源文件 | 从 **1024×1024** 母版下采样 | 保证任何尺寸都有足够细节；禁止从 256 放大 |
| 边距（安全区） | 内容不超出画布 **90%**（Windows 可低于 macOS） | 防止在列表视图里与其他图标挤在一起 |
| 文件命名 | `icon.ico` / `icon.icns`（小写） | electron-builder 默认查找名 |

### 4.1 设计约束

1. **16×16 必须可辨识** —— 这是最硬的一条。设计完必须缩到 16px 实际看一眼，
   如果此时只剩一个无法分辨的色块，方案就要推翻重做。
2. **避免细线条与细文字** —— 1px 线条在任何缩放下都会消失或糊掉。
   图标内**不要放汉字或字母**（16px 下必然不可读）。
3. **避免大面积渐变** —— 小尺寸下渐变会糊成单色，且增加 ico 体积。
4. **轮廓要实** —— 使用高对比度的实心形状，保证在浅色/深色任务栏下都清晰。
5. **与产品语义相关** —— 产品是「听课 + 字幕 + 学词」，
   建议方向：字幕条 / 对话气泡 / 声音波形 / 书本 + 波形。
   注意：**不要**做成纯耳机或纯麦克风，那会与录音类工具混淆。

---

## 五、存放位置与代码改动

### 5.1 文件存放

`electron-builder` 的 `directories.buildResources` **默认就是 `build`**，
而 electron-builder 的工程根目录是 **`frontend/`**（因为 `electron:build`
脚本在 `frontend/` 下执行）。

因此规范位置是：

```
frontend/
├── build/                    ← buildResources 目录（新增）
│   ├── icon.ico              ← Windows 应用图标（多尺寸容器）
│   ├── icon.icns             ← macOS（可选，若不做 mac 分发可省略）
│   ├── installerIcon.ico     ← NSIS 安装向导图标（可选，缺失则回落 icon.ico）
│   └── installerHeaderIcon.ico ← NSIS 安装向导页头图标（可选）
├── electron/
└── package.json
```

> ⚠️ **不要**放进 `frontend/release/`（那是 `directories.output`，构建产物目录，
> 且已被 `.gitignore` 忽略），也不要放进 `frontend/dist/`（Vite 产物，同样被忽略）。
>
> `frontend/build/` **不在** `.gitignore` 里，因此图标资产**会正常入库**——
> 这是期望行为：图标是源码资产，不是构建产物。

### 5.2 `frontend/package.json` 改动

在 `build.win` 中显式声明（当前该字段完全不存在）：

```jsonc
"win": {
  "target": [{ "target": "nsis", "arch": ["x64"] }],
  "icon": "build/icon.ico",            // ← 新增
  "artifactName": "tingkebanlv-${version}-setup.${ext}"
},
"nsis": {
  "oneClick": false,
  "perMachine": false,
  "allowToChangeInstallationDirectory": true,
  "createDesktopShortcut": true,
  "createStartMenuShortcut": true,
  "shortcutName": "听课伴侣",
  "deleteAppDataOnUninstall": false,
  "installerIcon": "build/installerIcon.ico",          // ← 新增（可选）
  "installerHeaderIcon": "build/installerHeaderIcon.ico", // ← 新增（可选）
  "uninstallerIcon": "build/installerIcon.ico"         // ← 新增（可选）
}
```

> 路径相对 `frontend/`（构建工作目录），**不要**写成 `../build/icon.ico`。

### 5.3 `frontend/electron/main.cjs` 改动

**改动一：设置 AppUserModelId（配合 §2 第 4 项）**

在应用启动的**早期**（`app.whenReady()` 之前，紧邻现有 `initLogFile()` 的位置）加入：

```js
// Windows 任务栏靠 AppUserModelID 归组窗口与识别图标。
// 必须与 package.json 的 build.appId 完全一致，否则任务栏会出现两个图标。
if (process.platform === 'win32') {
  app.setAppUserModelId('com.tingkebanlv.app');
}
```

并加一行启动日志便于排障：

```js
logLine(`[env] AppUserModelId=${process.platform === 'win32' ? 'com.tingkebanlv.app' : '(非 Windows，未设置)'}`);
```

**改动二：给主窗口显式传 `icon`（开发模式下有用）**

打包后 `.exe` 自带图标，`icon` 参数是冗余的；但在 **`npm run electron:dev`**
开发模式下，任务栏显示的是 Electron 自身图标。加 `icon` 可以让开发时也保持一致：

```js
mainWindow = new BrowserWindow({
  // ...现有配置
  icon: path.join(__dirname, '..', 'build', 'icon.ico'),  // ← 新增
  titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
});
```

> ⚠️ **打包后路径失效的坑**：`__dirname` 在打包后指向 `app.asar/electron/`，
> 而 `build/` 目录**不会**被打进 asar（它只作为 buildResources 在构建期使用）。
> 因此这行必须做**存在性判断**，否则打包版启动时可能因路径不存在而报错或静默失败：
>
> ```js
> const iconPath = path.join(__dirname, '..', 'build', 'icon.ico');
> const winIcon = fs.existsSync(iconPath) ? iconPath : undefined;
> ```
>
> 或者更简单：**只在非打包模式下传**（`app.isPackaged ? undefined : iconPath`）。
>
> 悬浮窗（`overlayWindow`）是 `frame: false` + `transparent: true` 的无边框窗口，
> **不需要也不应该**设置图标 —— 它不出现在任务栏。

---

## 六、验收标准

### 6.1 构建产物检查（不装也能先验）

| # | 验收项 | 验证方式 |
|---|---|---|
| 1 | `icon.ico` 含 7 档尺寸 | `python scripts/icon_assets.py check frontend/build/icon.ico` |
| 2 | 256 档为 PNG 压缩 | 同上（工具直接判 256 档编码，不再靠体积反推） |
| 3 | 色深为 32 位 RGBA | 同上（BMP 档判 32 位 + DIB 高度含 AND 掩码） |
| 4 | 1–3 项 + 第 7 项一键过 | `python scripts/icon_assets.py audit` |
| 5 | 16px 可辨识 | 缩到 16px 目视确认（**人工判断，不可跳过**） |
| 6 | 构建无图标警告 | `npm run electron:build` 输出中无 `default Electron icon is used` 提示 |
| 7 | 产物 exe 内嵌图标已替换 | 同上 `audit` 第 [2]/[3] 段；也可 `python scripts/icon_assets.py pe <exe>` |

> **为什么不再用 Pillow**：本项目 venv **没有装 PIL**（见 `README.md` 环境说明），
> 而这一项不值得为它引依赖。`scripts/icon_assets.py` 用纯标准库解 ico 容器与 PE
> 资源目录，第 1–3、7 项全部机器可判、退出码可入流水线。
>
> **第 6 项只能看构建日志**：electron-builder 回落默认图标时会打印一句
> `default Electron icon is used`。注意**回落不报错、不中断构建**——只看退出码会漏。
>
> **第 5 项是唯一必须人眼的**，没有任何工具能替代：把 ico 缩到 16px 实际看一眼，
> 只剩色块就推翻重做（§4.1 第 1 条）。

#### 6.1.1 换图后的一条命令（推荐流程）

```bash
# 1) 先验美术交付件本身（还没进仓库就能判，不通过直接打回）
<venv>/python.exe scripts/icon_assets.py check 美术交付/icon.ico

# 2) 覆盖 + 清构建缓存 + 重建
cp 美术交付/icon.ico frontend/build/icon.ico
rm -rf frontend/release/            # 不清会复用旧 exe，图标看起来"没换"
cd frontend && npm run electron:build

# 3) 验产物：ico 本体 + win-unpacked exe + NSIS 安装包
<venv>/python.exe scripts/icon_assets.py audit
```

`audit` 会同时对拍三份产物（详见 §8.4）。**2026-10-07 实测**：electron-builder
在 `win.icon` 生效时，应用 exe 与 NSIS 安装包**都保留全部 7 档** —— 所以
「安装包里档位不足」同样按 FAIL 判，不是提示。

### 6.2 实装检查（安装后逐一确认六处落点）

| # | 落点 | 期望 | 判据 |
|---|---|---|---|
| 1 | 安装向导（setup.exe 双击） | 显示新图标 | **人眼**（`audit` 只能保证安装包内嵌了 7 档） |
| 2 | 桌面快捷方式 | 新图标 | 机器：`audit` 第 [4] 段（继承 exe 图标）+ 报快捷方式存在性 |
| 3 | 开始菜单项「听课伴侣」 | 新图标 | 同上 |
| 4 | 任务栏（运行中） | 新图标，**且只有一个图标** | **人眼** + `audit` 第 [4] 段（AUMID 生效见 §6.3） |
| 5 | Alt+Tab 切换器 | 新图标 | **人眼** |
| 6 | 控制面板 → 程序和功能 → 「听课伴侣」 | 新图标 | 机器：`audit` 第 [4] 段扫注册表 `DisplayIcon` |

> **为什么 2/3/4/6 能机器判**：这四处**都不自带图标**，全部继承 `.exe` 内嵌图标
> （6 是 `nsis.uninstallerIcon` 缺失时回落应用图标）。因此只要 exe 里的
> `RT_GROUP_ICON` 是新的且 7 档齐，这四处必然跟着变——`audit` 直接读 PE 资源目录
> 就能给出结论，不必一个个点开看。
>
> 反过来说也成立：**这四处错了不会报错、不会崩溃**，只会在用户桌面上表现为
> 「有的地方是新图标、有的还是旧的」。这正是需要工具的原因（§8.4）。

### 6.3 AUMID 专项验证

| # | 步骤 | 期望 |
|---|---|---|
| 1 | 启动应用 → 右键任务栏图标 → 「固定到任务栏」 | 成功 |
| 2 | 完全退出应用 | 钉住的图标保留 |
| 3 | 从**钉住的图标**启动 | 应用打开，任务栏**不新增**第二个图标 |
| 4 | 查看 `%APPDATA%/听课伴侣/logs/electron.log` | 含 `[env] AppUserModelId=com.tingkebanlv.app` |

> 第 3 步是 AUMID 是否生效的**唯一可靠判据**。在修之前可以先做一次基线测试，
> 记录「当前是否真的会出现两个图标」——如果复现了，这就不只是推测而是已确认缺陷。

### 6.4 与既有验证脚本的衔接

**分两层，别混**：

| 层 | 谁负责 | 覆盖什么 | 现状 |
|---|---|---|---|
| 源码/配置层 | `frontend/tests/onboarding-aumid.test.cjs`（`npm test`） | AUMID 与 `build.appId` 逐字一致、`setAppUserModelId` 早于 `whenReady`、`win.icon` 指向 `build/icon.ico`、ico 容器是 7 档 | ✅ 已实现（2 条断言 + 9 条变异） |
| **产物层** | `scripts/icon_assets.py`（`backend/tests/test_icon_assets.py` 守工具本身） | ico 本体规格、**exe 里到底嵌了哪几档**、NSIS 安装包、已安装 exe / 快捷方式 / 注册表 `DisplayIcon` | ✅ 已实现（2026-10-07） |
| 实装环境层 | `scripts/verify_install.py` | 安装流程本身（57 步） | ⏳ **待接入**：见下 |

> **接入 `verify_install.py` 的待办**（换图时一并做，届时同一次运行即可验证）：
> 在打包验收流程里加一步，对 `frontend/build/icon.ico` 与 `release/win-unpacked/*.exe`
> 调 `icon_assets.py` 的同名函数（不要另写一份判定逻辑——两份判据必然漂移）。
> 这会改变 `verify_install` 的基线步数（当前 57 步 55 OK / 2 WARN / 0 FAIL），属预期。
>
> 为什么不现在加：工具与它的变异用例已经独立成立，不接 verify_install 也没有覆盖缺口；
> 而现在加进去却只能跑一次装箱验收才能确认（要装 700MB 应用），不如留到换图那一次
> 顺带落地并验证。

---

## 七、待决策项

| # | 决策点 | 建议 |
|---|---|---|
| 1 | 是否本版本支持 macOS 分发 | 建议**暂不做 icns**，`mac` 配置加注释说明；聚焦 Windows |
| 2 | 是否需要 `installerIcon` 等三个 NSIS 图标 | 建议**只做 `icon.ico`**，让 NSIS 全部回落到它，减少资源数量 |
| 3 | 图标美术资源由谁产出 | 需确认（自绘 / AI 生成 / 外包）。若走 AI 生成，需注意 16px 可辨识性通常不达标，必须人工重绘小尺寸 |
| 4 | 是否同时做托盘（Tray）图标 | 当前**无托盘功能**，不建议在 M3 引入 |
| 5 | `release/` 中已有旧构建产物 | 换图标后需**清理 `frontend/release/`** 再重新构建，避免缓存导致 exe 仍是旧图标 |

---

## 八、实现说明（M3 交付，2026-10-07）

### 8.1 已落地的链路

| 项 | 实现 |
|---|---|
| `app.setAppUserModelId` | `main.cjs` 顶部（建窗前 / `whenReady` 前）：`APP_USER_MODEL_ID = 'com.tingkebanlv.app'`，与 `package.json` 的 `build.appId` 逐字一致；`tests/onboarding-aumid.test.cjs` 做对拍 + 9 条变异用例（含「调用时机被挪到 whenReady 之后」） |
| 窗口 icon | `createWindow()` 传 `icon: APP_ICON`；悬浮窗（frame:false + transparent）**刻意不传**（不出现在任务栏） |
| 缺失兜底 | `resolveAppIconPath()`：打包后返回 `undefined`（build/ 不进 asar，exe 自带图标）；开发模式 `fs.existsSync` 判断 + try/catch，缺文件时回落 Electron 默认图标**不崩溃**，启动日志写明 |
| 打包配置 | `package.json` 新增 `build.win.icon = 'build/icon.ico'`；三个 NSIS 图标字段**刻意不加**（§七 待决策项 2：只做一份 icon.ico，让 NSIS 全部回落，减少资源数与格式转换风险） |
| ico 本体 | `frontend/build/icon.ico`：7 档尺寸（16/24/32/48/64/128/256，256 档 PNG 压缩，32 位 RGBA + 全透明背景），约 102 KB；由 `frontend/scripts/make-placeholder-icon.py` 生成（纯标准库，不依赖 Pillow） |
| 守卫 | `tests/onboarding-aumid.test.cjs` 另有 ico 容器头解析：档位数、宽高一致、256 档必须 PNG |

### 8.2 真机证据（2026-10-07 冒烟）

```
[env] AppUserModelId=com.tingkebanlv.app
[env] 应用图标=C:\...\frontend\build\icon.ico
```

### 8.3 待办（依赖美术资源交付）

- 覆盖占位 `icon.ico`（交付清单见 `frontend/build/README.md`：7 档、256 PNG、
  32 位 RGBA、sRGB、1024 母版下采样、16px 目视可辨识）
- 清 `frontend/release/` → `npm run electron:build` → 确认输出无
  `default Electron icon is used`
- `python scripts/icon_assets.py audit`（§6.1.1）
- §6.2 六处落点过一遍（2/3/4/6 机器判已含在 `audit` 里，1/4/5 需人眼）
- §6.3 AUMID 钉住图标准入验证（第 3 步是 AUMID 生效的唯一可靠判据）
- 接入 `scripts/verify_install.py`（§6.4）

### 8.4 产物层校验工具 `scripts/icon_assets.py`（2026-10-07 新增）

**为什么需要它**：上面那些源码层守卫全部建立在「文件就在那儿」之上，
**证明不了图标真的被塞进了 exe**。而六处落点中有 4 处（桌面 / 开始菜单 / 任务栏 /
控制面板）**继承 exe 内嵌图标**，exe 错了它们一起错，且**不报错、不崩溃** ——
只会在用户桌面上表现为「有的地方是新图标、有的还是默认图标」。

| 子命令 | 作用 |
|---|---|
| `check <ico>` | 校验一份 ico 本体：7 档齐全、位深/掩码、色彩类型、256 档 PNG 压缩、体积上限、目录项与 PNG 本体尺寸自洽 |
| `pe <exe> [...]` | 读 PE 资源目录 `RT_GROUP_ICON`，列出 exe 实际内嵌的尺寸档并与 `build/icon.ico` 对拍 |
| `audit` | 一键：ico 本体 + `release/win-unpacked/*.exe` + NSIS 安装包 + 已安装 exe/快捷方式/注册表 `DisplayIcon`，最后列出仍需人眼的 5 项 |

退出码 0 / 1 / 2（符合预期 / 有不符合项 / 文件不存在），可直接入流水线。

**实现要点**（踩过的坑，改这个文件前先读）：

1. 纯标准库，不引 Pillow/pefile —— 本机 venv 没有 PIL，这项不值得加依赖。
2. `IMAGE_RESOURCE_DIRECTORY` 的 `OffsetToData` 在**目录层级是相对资源目录起点的偏移**，
   只有最底层数据项里才是 RVA。写错就会读出垃圾尺寸，且不报错。
3. `GRPICONDIRENTRY` 是 **14 字节**（比 ico 目录项的 16 字节少），照 16 读会错位。
4. 解析异常必须转成「问题清单」而不是抛栈 —— 换图当天第一个被怀疑的该是图标，
   不是「工具崩了」。
5. **256 档必须 PNG 压缩这条要两个方向都判**。首版只在 `kind == 'png'` 分支里检查，
   于是「7 档齐、位深全对、只有 256 档是裸 BMP」——美术用普通导出工具存盘最常见的
   产物 —— 完全不报错。补上 `size == 256 and kind != 'png'` 的反向断言才抓住。

**工具自身的正确性**由 `backend/tests/test_icon_assets.py` 保证：12 条用例，
其中 10 条是**变异用例**（进程内构造坏 ico 字节流，不碰真实文件），改坏工具里
任何一条断言都必须让对应用例变红。实测两条新断言各做过一次变异探针，都是
「去掉即红、且只有那一条红」。改 `icon_assets.py` 后必须重跑：

```bash
<venv>/python.exe -m pytest backend/tests/test_icon_assets.py -v
```

> **已知局限**（别当成"验过了"，详见脚本头部注释）：
> exe 那一段**只对拍尺寸档位**，不校验内嵌图标的编码与位深；快捷方式只报存在性
> （它本来就不带图标）；目视类判据机器判不了。

