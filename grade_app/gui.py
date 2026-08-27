"""图形界面入口。

实现在 grade_app.ui 包里：
    ui/theme.py        设计令牌与 ttk 样式
    ui/widgets.py      圆角按钮、步骤指示器、电平表、提示条
    ui/dialogs.py      选择学生、设置、使用说明
    ui/main_window.py  主窗口
"""
from __future__ import annotations

from .ui.main_window import GradeApp, run

__all__ = ["GradeApp", "run"]
