# 成绩登记系统完善方案

日期：2026-08-26

## 目标

三条线并行推进，按顺序落地，每步跑通测试后再进下一步：

1. 修复体检发现的缺陷，并为每个缺陷补回归测试
2. 拆分过大的 `gui.py`，把录音逻辑从界面里剥离出来
3. 重做界面视觉，统一设计令牌

## 一、缺陷清单

### 数据安全

| # | 位置 | 问题 |
|---|---|---|
| 1 | `excel_io.load_sheet` | 不读取核对列 → 重开旧表后 `checked` 全为 `None`，`save_sheet` 把已有的 ✓／不一致清空 |
| 2 | `excel_io.save_sheet` | 对所有学生行写 `=SUM()`，未录分的行总分被写成 0 |
| 3 | `excel_io.save_sheet` | `score_cols` 为空时取 `[0]`／`[-1]` 抛 IndexError |

### 功能

| # | 位置 | 问题 |
|---|---|---|
| 4 | `speech.sherpa_model_dir` | `model_dir` 未转绝对路径，非项目目录启动时找不到模型并静默回退 |
| 5 | `gui.open_settings` | 保存时把自动挑选的麦克风索引写入 `config.json`，设备变化后失效 |
| 6 | `gui._build_table` | 右键只绑 `<Button-3>`，macOS 触控板右键是 `<Button-2>` |
| 7 | `gui._menu_activate` | 行号用 `cur.row + 2`，比 `_on_cell_select` 多 1 |
| 8 | `state._fill_scores` | 不记录最后填写的列，语音填分没有当前格高亮 |
| 9 | `state._fill_scores` | 分数变更后不复位 `checked`，旧的核对结论残留 |
| 10 | `excel_io._make_backup` | 备份数量无上限 |
| 11 | `main.check_environment` | 只检查 vosk 模型，默认引擎却是 sherpa |
| 12 | `README.md` | 默认引擎、依赖列表、目录结构与代码不一致 |

### 跨平台（macOS / Windows）

| # | 位置 | 问题 |
|---|---|---|
| 13 | `gui._open_excel_app` | 写死 `open` 命令，Windows 与 Linux 下失败 |
| 14 | `gui._show_mic_permission_hint` | 指引与跳转链接是 macOS 专属 |
| 15 | 全部界面代码 | 字体写死 `PingFang SC`，Windows 上回落到默认字体 |
| 16 | `gui._build_table` | 右键事件号在两个平台上不同 |
| 17 | 启动流程 | Windows 高分屏未开 DPI 感知，界面模糊 |
| 18 | 项目根目录 | 只有 `启动.command`，缺 Windows 启动脚本 |

### 复查追加

| # | 位置 | 问题 |
|---|---|---|
| 19 | `excel_io.save_sheet` | 只写不清：撤销掉的分数仍留在表格里，文件里的 `=SUM()` 继续把旧值算进总分 |
| 20 | `state._fill_scores` | 没带题号被丢弃的分数与「已忽略」提示对不上号，带题号填成功的分数也被算作忽略 |
| 21 | `state.check_total` | 从整句取最后一个数字，「一共92分」的「一」被当成题分，92 被填进空题 |

### 修复要点

- 缺陷 1：`load_sheet` 读核对列文本还原 `checked`；`save_sheet` 对 `checked is None` 的行不再清空已有内容
- 缺陷 2：仅对已有分数的学生写总分，其余行保持原样
- 缺陷 3：`score_cols` 为空时跳过总分写入
- 缺陷 9：填分／撤销／清空时把 `checked` 置回 `None`
- 缺陷 19：保存时清掉题号列里内存已无对应值的数值格（文字批注保留），分数清空后总分格一并清掉
- 缺陷 20：单独统计「无题号且无空题可填」的分数条数，提示改口播题号而非笼统说忽略
- 缺陷 21：`parser.split_at_total` 按总分关键词切句，总分只在关键词之后取

## 二、模块拆分

```
grade_app/
├── platform_support.py  平台差异：默认程序打开、字体族、DPI 感知、右键事件、麦克风设置入口
├── recorder.py          录音线程、软件 AGC、断句判定（不依赖 tkinter）
├── gui.py               薄入口 run()
└── ui/
    ├── theme.py         设计令牌与 ttk 样式
    ├── widgets.py       卡片、步骤指示器、电平表、提示条
    ├── main_window.py   布局装配与事件绑定
    └── dialogs.py       选择学生、设置、帮助
```

`recorder.py` 通过队列向界面推消息（`partial` / `final` / `level` / `error` 等），
接受任意满足 `begin/feed/finalize` 协议的引擎对象，可用假引擎做单元测试。

`main.py` 继续 `from grade_app.gui import run`，接口不变。

## 三、界面

设计令牌集中在 `ui/theme.py`：

- 中性色阶（背景、卡片、边框、主文字、次文字）
- 主色与四态语义色（信息／成功／警告／错误）
- 字号阶梯：标题 20 / 小标题 15 / 正文 13 / 辅助 11
- 间距阶梯：4 / 8 / 12 / 16 / 24
- 字体族按平台选取：macOS 用 PingFang SC，Windows 用 Microsoft YaHei UI，
  取不到时回落到 tk 默认族；实际可用性通过 `tkinter.font.families()` 校验

界面调整：

- 顶栏：左侧标题与当前文件名，右侧按钮组；主操作实心主色，次要操作描边
- 语音卡片：三步指示器（当前步高亮、已完成打勾）、当前学生大字加填写进度、
  电平块取代进度条
- 反馈区：带背景色的提示条，四态配色
- 表格：加大行高、斑马纹、当前行整行强调、待填列淡黄、核对列彩色徽章
- 弹窗沿用同一套令牌

不做深色模式：tkinter 手工维护双主题成本高于收益。

## 四、验收

- `python -m unittest discover tests` 全绿，且新增覆盖上述每个缺陷的用例
- `python main.py --check` 依据当前引擎给出正确结论，并列出运行平台
- 界面启动后三块区域（顶栏／语音卡片／表格）视觉统一
- 全仓库不残留平台专属调用：`open`、`x-apple.systempreferences`、写死字体名
  只允许出现在 `platform_support.py` 内
- Windows 与 macOS 各有一份启动脚本
