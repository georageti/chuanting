# 川听

扫描英文文档 → 识别生词和词组 → AI 给每个词编新的语境句并挖空 → 读给你听（空位“嘀”一声）→ 你拼写 → 按 FSRS 安排复习。

## 下载

[![下载 Mac 版](assets/btn-mac.svg)](https://github.com/georageti/chuanting/releases/latest/download/chuanting-mac.zip)
[![下载 Windows 版](assets/btn-windows.svg)](https://github.com/georageti/chuanting/releases/latest/download/chuanting-windows.zip)

下载后解压，打开里面的「先看我.txt」：先装 Python 3.10 以上，再双击「安装川听」（Mac 是 `.command`，Windows 是 `.bat`）。下载包里已经带好词典，不用再另外下载。所有版本见 [Releases](https://github.com/georageti/chuanting/releases)。

## 启动

**Mac**：在“应用程序”里打开 **川听**，或者按 `⌘ 空格` 用 Spotlight 搜“川听”。打开后可以在程序坞里的图标上右键，选“选项 → 在程序坞中保留”。快捷入口用 `python3 -I scripts/build_mac_app.py --install` 生成（装到 `~/Applications/川听.app`），项目文件夹挪了位置要重新生成一次。也可以双击 `启动川听.command`。

**Windows**：双击桌面或开始菜单里的 **川听**（安装脚本会建好这两个快捷方式），或者双击项目里的 `启动川听.bat`。不会弹黑色窗口。

两边都一样：

- 川听已经在运行时，再点只会打开网页；没在运行时，先在后台启动（约 1 秒），再打开网页。
- 用完在网页「设置」最下面点“关闭川听”。
- 在终端里启动：`.venv/bin/python run.py`（Windows：`.venv\Scripts\python.exe run.py`）。

第一次用之前，到「设置」里填 DeepSeek 的 API Key（platform.deepseek.com 申请）。不填也能用：程序会直接把原文句子挖空来练。

## 怎么用

1. **文档**：把 Word（.docx）、TXT、Markdown 文件拖进来，或者直接粘贴英文。程序会列出生词和词组，并附上原文例句。
   - **AI 逐句分析**（默认）：文档按句编号，每批约 2000 字符发给设置里的 AI。AI 读懂每一句，给出中文翻译，切出意群：短语动词、搭配、习语、固定表达，**词典里没有的也算**；难词和多义词按这句里的意思解释（比如 bank 是“河岸”）。每一处都核对：句号要对，片段要按顺序出现在原句里，对不上的丢掉。前 6 万字符交给 AI，更长的部分用词典。
   - **词组里的词不再拆成单词**：sit on the fence 里的 fence 不会单独列出；sb / sth 位置上的宾语照常算。
   - **单词**仍以 ECDICT 为底（还原变形、按考试等级和词频筛选），AI 挑出的词会带上“本文”意思，且不被等级筛掉。
   - 文档页有两个视图：“生词和词组”用来勾选加入生词本，“逐句解析”逐句显示翻译和意群释义。
   - 在“逐句解析”里可以自由加词：点意群勾选后“加入生词本”，或者用鼠标在句子里划选任意文字，上方会出现“加入生词本”。自选的文字先查词典，再让 AI 查它在这句里的意思；没经过 AI 分析的文档也能划选。
   - 朗读：扫描或重新分析后，服务端在后台先把每句语音合成好；打开逐句解析时网页也按顺序预取，点哪句就优先合成它后面几句。合成过的句子几毫秒就能播放。
   - 没填 Key、选了“不用 AI”、或者 AI 出错时，退回词典匹配并提示，词典里没有的短语这时认不出来；之后可以点“用 AI 重新分析”。
2. 勾选要练的词，点“加入生词本”。
3. **练习**：听句子，空位是“嘀”声，输入缺的词。可以用慢速、显示句子、中文提示、首字母这几个帮助，但用了帮助的那次只算“模糊”。
   - `Enter` 提交或进入下一个，`Esc` 重听。
   - 第一次练某个词时，AI 会现编语境句，之后会缓存。每个词有 2 句，轮流出现。
   - 每轮默认 **100 个**，首页或设置可改成 20 / 50 / 100 / 200 / 全部；“只复习”练到期词，想提前多练可用“全部过一遍”。每日新词额度独立保留。
   - **每天新词**可选 10 / 20 / 30 / 50 / 100 / 200 / 不限。选“不限”后可继续学完未学词；每轮仍按所选词数安排，想一次学完可把每轮词数设为“全部”。
   - **全部过一遍**覆盖整个生词本，**练这组**覆盖该组全部词条，不再截取前 20 / 30 个。每轮重新随机排序，轮内不重复。
   - 大词表每次准备 20 个词的语境句，自动接上下一批，进度显示本轮总词数。加载或保存失败可以重试；没有可用语境句的词会在结束页明确列出，不计作已练。
4. **导出 MP3 听力包**：每个词依次是：挖空句 → 停顿 → 答案和字母拼读 → 完整句 → 中文意思。适合通勤时听。
5. **生词本 → 导出到川词**：生成 `wordbook/1` 格式的文件，可以导入川词。

## AI 来源

| 选项 | 说明 |
|---|---|
| DeepSeek（默认） | 国内可以直连，价格便宜。模型 `deepseek-chat` |
| Claude | 用官方 `anthropic` SDK 调用，默认模型 `claude-opus-5-5`，JSON Schema 结构化输出。已开启服务端兜底（`fallbacks: "default"`）：请求被安全分类器误拦时自动换模型重试。国内需要开代理 |
| 其他 | 任何 OpenAI 兼容接口（Kimi、通义等），填接口地址（到 `/v1`）和模型名 |
| 不用 AI | 直接把原文句子挖空 |

也可以不在网页里填 Key，改用环境变量 `DEEPSEEK_API_KEY` 和 `ANTHROPIC_API_KEY`。

## 目录

```
chuanting/   后端（FastAPI）
  extract.py   分句、分词、单词和词组识别（允许变形、拆开写、sb/sth 占位）
  lexicon.py   词典查询、词形还原
  ai.py        生成语境句和挖空，以及原文兜底
  tts.py       edge-tts 朗读与缓存；离线兜底 macOS say / Windows SAPI；MP3 听力包（miniaudio 解码，lameenc 编码）
  srs.py       FSRS-5（与川打 srs.js 同一套公式和参数）
  store.py     SQLite 和设置
  server.py    接口
web/         网页（原生 JS，支持深色模式）
  fonts/       Funnel Display / Funnel Sans / DM Mono（本地，SIL OFL）
  icons/       taste-skill icons.mjs 下载的图标，来源见 icons-credits.json
scripts/build_dict.py   把 ECDICT 预处理成 data/dict.sqlite
scripts/setup.sh        Mac 一键安装
scripts/setup.ps1       Windows 一键安装（顺带建桌面、开始菜单快捷方式）
scripts/build_mac_app.py  生成 Mac 快捷入口 川听.app
scripts/make_ico.py     生成 Windows 图标 assets/chuanting.ico（在 Mac 上跑）
tests/run_tests.py      自测（111 项；加 --tts 会联网测语音和 MP3）
tests/test_sessions.py  练习范围、随机顺序、词数设置和分批完整性回归测试
tests/session-flow.test.js  浏览器练习状态流回归（加载/保存重试、跨批次和结束统计）
```

数据保存在：Mac `~/Library/Application Support/川听/`，Windows `%APPDATA%\川听\`（数据库、设置、语音缓存、导出的 MP3）。后台运行的日志：Mac 在 `~/Library/Logs/川听.log`，Windows 在数据目录里的 `川听.log`。设置文件里有 API Key。

## 安装

需要 Python 3.10 以上。词典数据太大，没有放进仓库，安装脚本会自动下载（ECDICT，约 63MB）并生成 `data/dict.sqlite`，最后跑一遍自测。

**Mac**

```bash
git clone https://github.com/georageti/chuanting.git
cd chuanting
bash scripts/setup.sh
```

**Windows 10 / 11**

1. 到 [python.org](https://www.python.org/downloads/) 安装 Python，安装时勾选 **Add python.exe to PATH**。
2. 下载本仓库（`git clone`，或者在 GitHub 页面点 Code → Download ZIP 并解压）。
3. 在项目文件夹里右键 `scripts\setup.ps1`，选 **使用 PowerShell 运行**。或者在 PowerShell 里运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

装完桌面和开始菜单里会出现“川听”。

**说明**：Windows 版没有在真机上测试过。代码里已经去掉了只有 macOS 才有的工具（音频解码改用 miniaudio，离线语音改用系统自带的 SAPI），Python 部分和 Mac 共用同一套代码。遇到问题欢迎提 issue。

## 界面设计

沿用川 Studio 主页的视觉系统（taste-skill）：绿色主色 #1f6b3a、Funnel 字体、soft 圆角、arrow 按钮，UI 卡片放在 sand 亮底上；
按钮悬停上浮、按下回弹，卡片悬停上浮 2px，链接下划线从左画出，减少动态效果时全部静止。

- taste-skill UI 片段：voice 的波形条（播放器）、stat 的柱状图（最近 14 天练习次数）
- react-bits：DecryptedText（揭晓答案时字母乱跳后落定）、CountUp（数字滚动）：参考它们的效果，用原生 JS 重写（没有引入 React）
- 图标：Lucide 线性图标、DeepSeek 和 Claude 的官方标志、Word / Markdown / TXT 文件图标（taste-skill icons.mjs）

视觉氛围按“朦胧柔焦美”参考：低饱和粉蓝光晕在底层缓慢漂移，侧栏、列表和卡片是磨砂玻璃，全屏一层很淡的胶片颗粒，主按钮和播放键带粉蓝光晕，大标题后有错位叠影；练习时侧栏和进度退到朦胧里只留卡片清晰，悬停列表时其余行变朦胧，内容出现时从模糊对焦，换卡片带横向运动模糊。正文对比度不降；减少动态效果时动画全停。

## 致谢

- 词典：[ECDICT](https://github.com/skywind3000/ECDICT)（MIT），许可证在 `data/raw/ECDICT-LICENSE`
- 语音：[edge-tts](https://github.com/rany2/edge-tts)（微软 Edge 朗读服务）；连不上时退回 macOS 自带的 `say`
- 动效参考：[React Bits](https://reactbits.dev)（DecryptedText、CountUp）
- 思路参考：LuteOrg/lute-v3（多词词组匹配）、vocabsieve（已会词过滤）、tomato-vocast（留停顿让你先猜）、podcastfy（大模型写稿再合成语音）

## 许可证

川听的代码以 [MIT 许可证](LICENSE) 发布。

随项目一起分发或使用的第三方内容，各自沿用原来的许可：

- 词典 ECDICT：MIT，见 `data/raw/ECDICT-LICENSE`（词典文件本身由 `setup.sh` 下载，不在仓库里）
- 字体 Funnel Display、Funnel Sans、DM Mono：SIL Open Font License
- 图标：Lucide（ISC）、VSCode Icons（MIT）、SVG Logos（CC0），出处见 `web/icons/icons-credits.json`；DeepSeek、Claude 的标志是其所有者的商标，只用来标明对应的服务
