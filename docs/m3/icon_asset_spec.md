# M3 · 应用图标资源规范（Icon Asset Spec）

> 状态：**加载链路已实现（2026-10-07）**；`icon.ico` 当前为 **v4.1 海獭版**
> （生成器 `frontend/build/icon-source/build_otter.py`，单一数据源：master 256~32
> 完整设计 + 16/24px 独立剪影；v4.1 于 2026-10-12 给 16px 剪影加了像素脸，
> 见 `frontend/build/README.md`「当前状态」）。换图 / 美化迭代直接改脚本参数重跑。
> **换图工具链已就位（2026-10-07）**：产物层校验 `scripts/icon_assets.py`（§8.4）
> + 换图一条命令（§6.1.1），美术资源到位后按 §8.3 执行即可。
> **📌 换图时请直接看 §九（§8.5「图标交付后流水线 · 唯一清单来源」）—— 那是唯一执行清单，
> 本章其他位置的命令均为背景说明，不再作为验收依据。**
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

> ⚠️ **本条是精简示意，不是执行清单。** 换图请照 **§8.5** 逐步执行
> （关键在于 `audit` 必须放在清 `release/` + 重新打包**之后**，否则会读到旧产物假绿）。

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

行号与 §二 的落点编号一一对应（§二 把「任务栏 / Alt+Tab」算作同一处落点 4，
这里为便于逐项目检拆成两行）。**完整执行顺序见 §8.5 —— 那才是唯一执行清单。**

| §二落点 | 具体看什么 | 期望 | 判据 |
|---|---|---|---|
| 1 | 应用 exe 内嵌图标（资源管理器里看 `听课伴侣.exe`） | 新图标 | 机器 `audit` 第 [2] 段 + 人眼 |
| 2 | 桌面快捷方式 | 新图标 | 机器 `audit` 第 [4] 段（继承 exe 图标）+ 报快捷方式存在性 |
| 3 | 开始菜单项「听课伴侣」 | 新图标 | 同上 |
| 4 | 任务栏（运行中） | 新图标，**且仅出现单个图标（AUMID 校验目标）** | **人眼** + `audit` 第 [4] 段（AUMID 生效见 §6.3） |
| 4 | Alt+Tab 切换器 | 新图标 | **人眼** |
| 5 | 安装向导界面（双击 setup.exe） | 显示新图标 | **人眼**（`audit` 只能保证安装包内嵌了 7 档） |
| 6 | 控制面板 → 程序和功能 →「听课伴侣」**卸载项图标** | 新图标 | 机器：`audit` 第 [4] 段扫注册表 `DisplayIcon` |

> ⚠️ **第 6 项的准确表述**：指的是「控制面板 → 程序和功能」列表里那一行的**卸载项图标**
> （读注册表 `Uninstall\...\DisplayIcon`，`nsis.uninstallerIcon` 缺失时回落应用图标）。
> **不是**「控制面板窗口自身的图标」——那是 Windows 系统程序的图标，与本应用无关，改不了。
>
> **为什么 1/2/3/4/6 能机器判**：除 exe 本体外，这几处**都不自带图标**，全部继承 `.exe`
> 内嵌图标。因此只要 exe 里的 `RT_GROUP_ICON` 是新的且 7 档齐，它们必然跟着变——`audit`
> 直接读 PE 资源目录就能给出结论，不必一个个点开看。
>
> 反过来说也成立：**这几处错了不会报错、不会崩溃**，只会在用户桌面上表现为
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

**v4.1 增补（2026-10-12，V6-3）**：16px 剪影加「两眼 + 鼻」像素脸。
产物层机器判据全部通过 —— `icon_assets.py check`（8 档 / 256 PNG / 体积不变 82.1 KB）、
`test_icon_assets.py` **12 passed**、前端 `npm test` **205 pass**（含 ico 容器守卫）。
16px 可辨识性按 §6.1 第 5 项用**像素级放大图**复核（NEAREST ×16，浅 / 深双底）：
v4 是「蓝圆 + 黄帽 + 白块」（有帽无脸），v4.1 白脸上有两个清晰眼点 + 鼻杠。
⚠️ 实验记录（勿重蹈）：在设计空间画五官（眼距 32 / 眼距 40 两轮参数）都会被
超采样 + LANCZOS 涂抹成横带并切碎白脸 —— 16px 的表情只能在 16×16 网格上
**直接贴像素**（`s16_pixel_face()`），实现与坐标反推依据见 `build_otter.py` 函数注释。

### 8.3 待办（依赖美术资源交付）

> **执行时以 §8.5（§九）为准 —— 那是唯一执行清单**，本列表只是要点索引。

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


---

## 九、图标交付后流水线 · 唯一清单来源（§8.5，2026-10-07 用户确认）

> ### ⚠️ 本章节为**唯一执行清单**
>
> 美术资源交付后，**只认这一份**。§6.1 / §6.1.1 / §8.3 是设计与背景说明，
> 任何**其他副本、历史清单、聊天里贴过的版本**（含 §6.1.1 那条精简版命令）
> **都不再作为验收依据** —— 目的是防止多份清单口径漂移导致的版本冲突。
> 若本章与其他章节冲突，**以本章为准**，并回头修其他章节。

### 8.5.0 前置条件

- 美术交付**完整 `.ico`**：7 档（16 / 24 / 32 / 48 / 64 / 128 / 256）、
  256 档 **PNG 压缩**、**32 位 RGBA** + 全透明背景、sRGB、1024 母版下采样。
- 本机环境：`<venv>` = `安装空间/venv/Scripts/python.exe`（详见 `README.md`）。
- 起始状态：`frontend/release/` 里是**上一次**的构建产物（这是正常的，后面要清掉）。

### 8.5.1 一、资源替换

```bash
# 1) 先验交付件本身 —— 还没进仓库就能判，不通过直接退回美术，禁止强行放行
python scripts/icon_assets.py check <美术交付目录>/icon.ico

# 2) 通过后覆盖
cp <美术交付目录>/icon.ico frontend/build/icon.ico
```

3）**核对 AUMID 未被改动**：`frontend/electron/main.cjs` 的 `APP_USER_MODEL_ID`
与 `frontend/package.json` 的 `build.appId` 必须逐字一致（`npm test` 里的
`onboarding-aumid` 守卫会拦；见 §6.3）。

> ### 🚨 时序硬约束：**这一步不要跑 `audit`**
>
> `audit` 的第 [2] / [3] / [4] 段分别读 `release/win-unpacked/*.exe`、
> `release/*setup.exe`、已安装的 exe —— 覆盖 ico 之后、**清 `release/` 并重新打包之前**，
> 这些路径里躺的还是**上一次的旧产物**。
>
> 更糟的是：旧产物本身也是 7 档，于是 `audit` **照样打印「结论：机器可判部分全部通过 ✓」**
> —— 这是**假绿**，它验的是旧图标，与新图标毫无关系。
>
> **正确顺序：替换 → 清 `release/` → 重新打包 → 才跑 `audit`**（即 §8.5.2 之后）。
> 替换后想立刻验交付件，用 `check`（只读 ico 本体，不看产物）。

### 8.5.2 二、清理 + 打包

```bash
# 1) 清旧构建（不清会复用旧 exe，图标看起来"没换"）
rm -rf frontend/release/

# 2) 打包（在仓库根执行；脚本内部会 cd frontend）
npm run electron:build
```

3）**看构建日志**：**不得出现** `default Electron icon is used`
—— electron-builder 回落默认图标时**不报错、不中断构建**，只看退出码会漏。

4）**确认 `release/win-unpacked/*.exe` 的时间戳是刚才** —— 时间戳是「确实重新构建过」
的唯一判据（踩过的坑：构建日志报成功但产物没变）。

### 8.5.3 三、产物层机器校验（现在才跑）

```bash
python scripts/icon_assets.py audit
```

| 段 | 读什么 | 期望 |
|---|---|---|
| [1] | `frontend/build/icon.ico` 本体 | 7 档齐 / 256 档 PNG / 32 位 RGBA / 体积 < 400KB |
| [2] | `release/win-unpacked/*.exe` 内嵌 `RT_GROUP_ICON` | 与 ico 档位一致 |
| [3] | `release/*setup.exe`（NSIS 安装包） | 同上（实测也保留全 7 档） |
| [4] | 已安装 exe / 快捷方式 / 注册表 `DisplayIcon` | 需先装，见 §8.5.4 |

> **档位不足一律按 FAIL 处理**，不是提示 —— 继承 exe 图标的那几处会一起错。

### 8.5.4 四、打包端到端验收

```bash
# ① 完整端到端（57 步：安装 → 启动 → 150s 稳定性 → 向导全流程 → 卸载）
<venv>/python.exe scripts/verify_install.py \
  --installer frontend/release/tingkebanlv-<版本>-setup.exe

# ② 需要把应用留在机器上做 §8.5.5 人工落点目检时，**单独**再跑一次带 --keep-installed 的
<venv>/python.exe scripts/verify_install.py \
  --installer frontend/release/tingkebanlv-<版本>-setup.exe \
  --keep-installed
```

- `--installer` 是**必填**参数，路径按 `release/` 里的实际文件名（`artifactName` 模板
  为 `tingkebanlv-${version}-setup.${ext}`）。
- 🚨 **`--keep-installed` 不是「正常验收 + 保留安装」的开关**：它会在后端自检之后
  **提前 `return 0`**（verify_install.py 的 keep_installed 分支），**跳过启动应用、
  150s 稳定性观察、向导全流程等约 41 步**。2026-10-07 图标彩排实测：带它跑只出
  **16 步 / 33s**，看起来全绿，其实端到端一步没跑。所以**必须先跑 ①**，再视需要跑 ②。
- 报告落 `packaging/verify/report.json`（`--report` 可改路径；已被 `.gitignore`，属本地证据）。

### 8.5.5 五、六处落点核验

表格见 **§6.2**（落点编号与 §二 对齐）。机器判的部分由 `audit` 覆盖，
以下**四项必须人眼**，不可省略：

| 人眼项 | 判据 |
|---|---|
| 16px 可辨识 | 缩到实际 16px 看一眼；只剩色块 → 退回美术重做 |
| 任务栏**仅出现单个图标** | 从钉住的图标启动，任务栏不新增第二个（AUMID 唯一可靠判据） |
| Alt+Tab 切换器 | 显示新图标 |
| 安装向导界面 | 双击 setup.exe，向导窗口显示新图标 |

### 8.5.6 六、收尾

```bash
# 1) 全量回归
<venv>/python.exe -m pytest backend/tests/            # 含 test_icon_assets.py
cd frontend && npm test && npx tsc --noEmit
```

2）**提交 git**；**同步仓库内副本** `tingke-banlv/`，再逐文件对拍 —— 对拍基准必须用
git 跟踪文件列表，**不要用 `find`**（`find` 会把 `.gitignore` 的构建证据全报成
「副本缺失」，噪声会淹没真实差异）：

```bash
git -c core.quotepath=false ls-files | while read -r f; do cmp -s "$f" "tingke-banlv/$f" || echo "DIFF: $f"; done
```

> `-c core.quotepath=false` 不能省：git 默认把中文路径输出成八进制转义，
> 会让中文名文件被误判为「副本缺失」。

3）**文档记录**：图标版本 + `audit` 全量输出 + 目检截图 → 追加到 **§8.2 真机证据**
与 §8.3 勾选。

4）**卸载验收安装**（若未使用 `--keep-installed` 则跳过）：

> ⚠️ **NSIS 卸载器是「自拷贝到 `%TEMP%/~nsu.tmp` 再后台异步删目录」**。
> `subprocess.run(uninstaller, '/S', '/currentuser')` 返回 **只代表引导器退出**，
> 真正的卸载还在后台跑。此时若顺手清 `~nsu.tmp`，会**打断卸载**、留下半个安装目录
> （`resources`、`*.pak`、`Uninstall 听课伴侣.exe` 等）。
> **正确顺序：跑卸载 → 等安装目录自然清空 → 再清临时目录。**

### 8.5.7 失败兜底（任意环节报错）

| 现象 | 处置 |
|---|---|
| `check` / `audit` 不通过 | **退回美术调整 ico 重新导出，禁止强行放行** |
| `audit` 报「档位不足」但你没换图标 | 先看 `release/` 里产物的时间戳是不是旧的；**别急着退回美术** |
| 打包后部分位置图标不变 | 确认确实清了 `release/`，再**完整重新 build**，不要增量打包 |
| 桌面 / 开始菜单仍是旧图标，而 exe 已确认是新图标 | **Windows 图标缓存**：注销重登（或清 `%LOCALAPPDATA%\IconCache.db`）后再验。不是打包问题 |
| `audit` 第 [4] 段说「未找到注册表卸载项」 | 说明应用当前**没装**（或已被卸载），**不是失败**；装完再跑 |
| `verify_install` 报安装包不存在 | 核对 `release/` 下实际文件名（含版本号），别照抄示例 |

### 8.5.8 关于「用哪个 python」

| 用途 | 用什么 | 原因 |
|---|---|---|
| `icon_assets.py`、`verify_install.py` | **裸 `python` 即可** | 两者**纯标准库**（不引 PIL / pefile / 第三方包），任何 Python 3.10+ 都能跑 |
| `pytest`（含 `test_icon_assets.py`） | **必须 `<venv>/python.exe`** | venv 之外没有装 pytest，裸 `python -m pytest` 会 `No module named pytest` |

> 完整流水线至此结束。**图标验收全部通过后**，再进入 P3 长跑验收
> （等待 ≥10min 真实网课音频 + 人工逐字稿）。
