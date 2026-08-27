"""生成一张带数据的实例成绩表（实例成绩表.xlsx），用于体验软件各项功能。

演示点：
- 总分 =SUM 公式（Excel/WPS 打开自动重算）
- 核对一致 -> 绿色 ✓（如 王小明 行）
- 核对不一致 -> 「不一致」红底红字（如 张伟 行）
- 两位「张三」 -> 念「张三」时弹窗选择（重名演示）
- 姓名含汉字数字（李四） -> 不会误当成分数

用法：python make_instance.py
"""
from __future__ import annotations

from openpyxl import Workbook

from grade_app.excel_io import SheetModel, StudentRecord, save_sheet

CFG = {
    "strip_prefix": True, "strip_suffix": True, "score_cutoff": 0.55,
    "write_formula": True, "write_checked": True, "auto_save": False,
    "check_header": "核对",
}

HEADER = ["姓名", "第一题", "第二题", "第三题", "第四题", "总分"]

# 全空模板：12 名学生，分数/总分/核对全部留空，供老师从头语音录入
ROWS = [
    ("王小明", {}, None),
    ("李四",   {}, None),
    ("张伟",   {}, None),
    ("刘洋",   {}, None),
    ("陈静",   {}, None),
    ("张三",   {}, None),   # 重名一：念「张三」时弹窗选择
    ("赵磊",   {}, None),
    ("孙丽",   {}, None),
    ("周杰",   {}, None),
    ("吴敏",   {}, None),
    ("郑浩",   {}, None),
    ("张三",   {}, None),   # 重名二
]


def main() -> str:
    path = "实例成绩表.xlsx"

    # 1) 先建带表头的空表并写入姓名（save_sheet 只填分数/总分/核对，不写姓名）
    wb = Workbook()
    ws = wb.active
    ws.title = "成绩表"
    ws.append(HEADER)                      # 核对列表头由 save_sheet 自动补
    for i, (name, _, _) in enumerate(ROWS):
        ws.cell(row=i + 2, column=1, value=name)
    wb.save(path)

    # 2) 构造内存模型并保存（写分数 + 总分公式 + 核对标记）
    students = []
    for i, (name, scores, checked) in enumerate(ROWS):
        students.append(StudentRecord(
            row=i + 1,                     # 0 基行号：数据首行索引 1（Excel 第 2 行）
            name=name,
            scores=dict(scores),
            total=None,
            checked=checked,
        ))
    model = SheetModel(
        path=path, sheet_name="成绩表", header=list(HEADER),
        name_col=0, score_cols=[1, 2, 3, 4], total_col=5, check_col=6,
        students=students,
    )
    save_sheet(model, CFG, write_formula=True)

    print(f"实例成绩表已生成: {path}（12 名学生，4 道题）")
    print("演示点：总分自动算（Excel 打开即出）、✓ 绿勾、『不一致』红字（张伟）、"
          "两位『张三』重名弹窗")
    return path


if __name__ == "__main__":
    main()
