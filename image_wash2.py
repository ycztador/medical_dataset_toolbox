# rename_gui_with_format_convert.py
# -*- coding: utf-8 -*-
"""
影像数据重命名/转存 GUI 程序（新增：真正的格式转换，例如 .mha → .nii.gz）
- 支持 .nii.gz 等复合扩展名识别
- “转换扩展名”现在默认为【读取并另存为】（非简单改后缀）
- 可选：转换完成后删除源文件
- 仍然保留原有的“严格匹配（当且仅当）”与 预览 → 再执行 的工作流

依赖：
    pip install SimpleITK

作者：你
日期：2025-09-23
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# 可选依赖：SimpleITK 用于医学影像读写（真正的格式转换）
try:
    import SimpleITK as sitk
    SITK_AVAILABLE = True
except Exception:
    SITK_AVAILABLE = False

# ------------------------------ 工具函数 ------------------------------

def split_ext_composite(path, matched_exts):
    """拆分复合扩展名（优先匹配更长的扩展，如 .nii.gz）。
    返回：dirpath, base(不含扩展名), ext(包含点)
    """
    dirpath, filename = os.path.split(path)
    lower = filename.lower()
    for ext in matched_exts:
        if lower.endswith(ext):
            base = filename[: -len(ext)]
            return dirpath, base, filename[-len(ext):]
    base, ext = os.path.splitext(filename)
    return dirpath, base, ext


def normalize_ext_list(ext_text):
    if not ext_text.strip():
        return []
    items = [e.strip() for e in ext_text.split(",") if e.strip()]
    norm, seen = [], set()
    for e in items:
        if not e.startswith("."):
            e = "." + e
        e = e.lower()
        if e not in seen:
            seen.add(e)
            norm.append(e)
    # 复合扩展优先匹配更长者
    norm.sort(key=len, reverse=True)
    return norm


def safe_insert(s, pos, token):
    n = len(s)
    if pos < 0:
        pos = n + pos
    pos = max(0, min(n, pos))
    return s[:pos] + token + s[pos:]


def safe_delete(s, start, length):
    if length <= 0:
        return s
    n = len(s)
    if start < 0:
        start = n + start
    start = max(0, min(n, start))
    end = max(start, min(n, start + length))
    return s[:start] + s[end:]


def apply_rename_rules(base_name, add_cfg, del_cfg, repl_list):
    new_name = base_name
    if add_cfg[0]:
        pos, token = add_cfg[1], add_cfg[2]
        new_name = safe_insert(new_name, pos, token)
    if del_cfg[0]:
        st, ln = del_cfg[1], del_cfg[2]
        new_name = safe_delete(new_name, st, ln)
    for old, new in repl_list:
        if old:
            new_name = new_name.replace(old, new)
    return new_name


def detect_conflicts(mapping):
    msgs = []
    targets = {}
    for old_p, new_p in mapping:
        if old_p == new_p:
            continue
        if new_p in targets:
            msgs.append(f"[目标重复] {new_p}\n  ↳ {targets[new_p]}\n  ↳ {old_p}")
        else:
            targets[new_p] = old_p
    for old_p, new_p in mapping:
        if old_p == new_p:
            continue
        if os.path.exists(new_p) and os.path.abspath(new_p) != os.path.abspath(old_p):
            msgs.append(f"[已存在文件] {new_p}（将覆盖不同源文件）")
    return (len(msgs) > 0, msgs)


def ensure_parent_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def convert_image_format(in_path, out_path):
    """使用 SimpleITK 读取并写出影像，实现真正的格式转换。
    - 自动保留 spacing / direction / origin 等元数据
    - 对 .nii.gz 会使用压缩写出（传 True）
    """
    if not SITK_AVAILABLE:
        raise RuntimeError("未安装 SimpleITK：请先 pip install SimpleITK")
    img = sitk.ReadImage(in_path)
    # 对于 NIfTI，直接根据文件名后缀决定写出编码；传 True 启用压缩
    sitk.WriteImage(img, out_path, True)


# ------------------------------ GUI 应用 ------------------------------

class RenameApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("课题组影像数据批量重命名/转存（支持 .nii.gz 等复合扩展 | 真正格式转换）")
        self.minsize(1180, 760)

        self.root_dir = tk.StringVar()
        self.ext_text = tk.StringVar(value=".nii,.nii.gz,.mha,.nrrd")
        self.all_files = []
        self.filtered_files = []
        self.preview_pairs = []

        self.filter_entries = []
        self.replace_rows = []

        self.add_enabled = tk.BooleanVar(value=False)
        self.del_enabled = tk.BooleanVar(value=False)
        self.repl_enabled = tk.BooleanVar(value=False)
        self.change_ext_enabled = tk.BooleanVar(value=True)  # 开启则触发“另存为”工作流

        # 新增：严格匹配 & 转存选项
        self.strict_exact = tk.BooleanVar(value=False)
        self.convert_mode_enabled = tk.BooleanVar(value=True)  # 读取并另存为（强烈建议开启）
        self.delete_source_after_convert = tk.BooleanVar(value=False)
        self.skip_if_same_dtype_ext = tk.BooleanVar(value=False)  # 可选：同类型时跳过

        self.add_pos = tk.StringVar(value="0")
        self.add_token = tk.StringVar(value="")
        self.del_start = tk.StringVar(value="0")
        self.del_len = tk.StringVar(value="1")
        self.new_ext_text = tk.StringVar(value=".nii.gz")

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        top = ttk.Frame(self); top.pack(fill="x", **pad)
        ttk.Label(top, text="根目录：").pack(side="left")
        ttk.Entry(top, textvariable=self.root_dir, width=62).pack(side="left", padx=4)
        ttk.Button(top, text="选择...", command=self.choose_root).pack(side="left")

        extf = ttk.Frame(self); extf.pack(fill="x", **pad)
        ttk.Label(extf, text="扩展名（逗号分隔）：").pack(side="left")
        ttk.Entry(extf, textvariable=self.ext_text, width=44).pack(side="left", padx=4)
        ttk.Button(extf, text="展示文件", command=self.scan_files).pack(side="left", padx=6)
        self.scan_status = ttk.Label(extf, text="未扫描"); self.scan_status.pack(side="left", padx=12)

        listf = ttk.LabelFrame(self, text="匹配扩展名的文件（全部）")
        listf.pack(fill="both", expand=True, **pad)
        self.all_tree = ttk.Treeview(listf, columns=("path",), show="headings", height=8)
        self.all_tree.heading("path", text="文件路径")
        self.all_tree.column("path", anchor="w", width=1040)
        self.all_tree.pack(fill="both", expand=True, side="left")
        vs1 = ttk.Scrollbar(listf, orient="vertical", command=self.all_tree.yview)
        self.all_tree.configure(yscrollcommand=vs1.set); vs1.pack(side="right", fill="y")

        filtf = ttk.LabelFrame(self, text="匹配筛选")
        filtf.pack(fill="x", **pad)

        left_box = ttk.Frame(filtf); left_box.pack(side="left", fill="x", expand=True)
        self.filters_holder = ttk.Frame(left_box); self.filters_holder.pack(fill="x")
        btns = ttk.Frame(left_box); btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="+ 添加筛选框", command=self.add_filter_box).pack(side="left", padx=4)
        ttk.Button(btns, text="查找", command=self.apply_filters).pack(side="left", padx=4)
        ttk.Button(btns, text="清空筛选", command=self.clear_filters).pack(side="left", padx=4)

        right_box = ttk.Frame(filtf); right_box.pack(side="left", padx=12)
        ttk.Checkbutton(right_box, text="当且仅当（严格等于文件名，不含扩展名）",
                        variable=self.strict_exact).pack(side="left")

        self.find_status = ttk.Label(filtf, text="未查找"); self.find_status.pack(side="left", padx=12)

        resf = ttk.LabelFrame(self, text="查找结果（将对这些文件执行改名/转存）")
        resf.pack(fill="both", expand=True, **pad)
        self.find_tree = ttk.Treeview(resf, columns=("path",), show="headings", height=8)
        self.find_tree.heading("path", text="文件路径")
        self.find_tree.column("path", anchor="w", width=1040)
        self.find_tree.pack(fill="both", expand=True, side="left")
        vs2 = ttk.Scrollbar(resf, orient="vertical", command=self.find_tree.yview)
        self.find_tree.configure(yscrollcommand=vs2.set); vs2.pack(side="right", fill="y")

        rulef = ttk.LabelFrame(self, text="规则设置")
        rulef.pack(fill="x", **pad)

        addf = ttk.Frame(rulef); addf.pack(fill="x", pady=2)
        ttk.Checkbutton(addf, text="增加", variable=self.add_enabled).pack(side="left")
        ttk.Label(addf, text="位置 index：").pack(side="left")
        ttk.Entry(addf, textvariable=self.add_pos, width=8).pack(side="left", padx=4)
        ttk.Label(addf, text="插入字符串：").pack(side="left")
        ttk.Entry(addf, textvariable=self.add_token, width=26).pack(side="left", padx=4)
        ttk.Label(addf, text="（负索引从右向左计数）").pack(side="left")

        delf = ttk.Frame(rulef); delf.pack(fill="x", pady=2)
        ttk.Checkbutton(delf, text="减少", variable=self.del_enabled).pack(side="left")
        ttk.Label(delf, text="起始 index：").pack(side="left")
        ttk.Entry(delf, textvariable=self.del_start, width=8).pack(side="left", padx=4)
        ttk.Label(delf, text="删除长度：").pack(side="left")
        ttk.Entry(delf, textvariable=self.del_len, width=8).pack(side="left", padx=4)
        ttk.Label(delf, text="（负索引从右向左计数）").pack(side="left")

        replf_outer = ttk.Frame(rulef); replf_outer.pack(fill="x", pady=2)
        ttk.Checkbutton(replf_outer, text="替换（可多对，全局）", variable=self.repl_enabled).pack(side="left")
        self.repl_holder = ttk.Frame(rulef); self.repl_holder.pack(fill="x", pady=2)
        btns2 = ttk.Frame(rulef); btns2.pack(fill="x")
        ttk.Button(btns2, text="+ 添加替换对", command=self.add_replace_row).pack(side="left", padx=4)
        ttk.Button(btns2, text="清空替换对", command=self.clear_replace_rows).pack(side="left", padx=4)

        extf2 = ttk.Frame(rulef); extf2.pack(fill="x", pady=2)
        ttk.Checkbutton(extf2, text="转换扩展名（建议 .nii.gz）",
                        variable=self.change_ext_enabled).pack(side="left")
        ttk.Label(extf2, text="新扩展名：").pack(side="left")
        ttk.Entry(extf2, textvariable=self.new_ext_text, width=18).pack(side="left", padx=4)
        ttk.Label(extf2, text="示例：.nii.gz / .mha / .nrrd").pack(side="left")

        convf = ttk.Frame(rulef); convf.pack(fill="x", pady=2)
        ttk.Checkbutton(convf, text="读取并另存为（真正格式转换，非改后缀）",
                        variable=self.convert_mode_enabled).pack(side="left")
        ttk.Checkbutton(convf, text="转换后删除源文件（谨慎）",
                        variable=self.delete_source_after_convert).pack(side="left", padx=12)
        ttk.Checkbutton(convf, text="当新旧均为 NIfTI 时可跳过（可选）",
                        variable=self.skip_if_same_dtype_ext).pack(side="left", padx=12)

        actf = ttk.Frame(self); actf.pack(fill="x", **pad)
        ttk.Button(actf, text="转换预览", command=self.build_preview).pack(side="left")
        ttk.Button(actf, text="清空预览", command=self.clear_preview).pack(side="left", padx=6)
        ttk.Button(actf, text="执行", command=self.do_process).pack(side="left", padx=12)
        self.proc_status = ttk.Label(actf, text=""); self.proc_status.pack(side="left", padx=12)

        prevf = ttk.LabelFrame(self, text="转换预览（Original → New）")
        prevf.pack(fill="both", expand=True, **pad)
        self.prev_tree = ttk.Treeview(prevf, columns=("old", "new"), show="headings", height=12)
        self.prev_tree.heading("old", text="原路径")
        self.prev_tree.heading("new", text="新路径")
        self.prev_tree.column("old", anchor="w", width=560)
        self.prev_tree.column("new", anchor="w", width=560)
        self.prev_tree.pack(fill="both", expand=True, side="left")
        vs3 = ttk.Scrollbar(prevf, orient="vertical", command=self.prev_tree.yview)
        self.prev_tree.configure(yscrollcommand=vs3.set); vs3.pack(side="right", fill="y")

        logf = ttk.LabelFrame(self, text="日志")
        logf.pack(fill="both", expand=False, **pad)
        self.log_text = tk.Text(logf, height=9); self.log_text.pack(fill="both", expand=True)

        self.add_filter_box()
        self.add_replace_row()

    # ---------- 事件处理 ----------
    def choose_root(self):
        d = filedialog.askdirectory(title="选择数据集根目录")
        if d:
            self.root_dir.set(d)

    def scan_files(self):
        root = self.root_dir.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("提示", "请先选择有效的根目录。"); return
        exts = normalize_ext_list(self.ext_text.get())
        if not exts:
            messagebox.showwarning("提示", "请先输入至少一个扩展名（如 .nii.gz）。"); return

        self.all_files.clear()
        for dp, _, fns in os.walk(root):
            for fn in fns:
                lower = fn.lower()
                if any(lower.endswith(e) for e in exts):
                    self.all_files.append(os.path.join(dp, fn))

        self._fill_tree(self.all_tree, [(p,) for p in self.all_files])
        self.scan_status.configure(text=f"共找到 {len(self.all_files)} 个文件")
        self.log(f"[扫描完成] 扩展名={exts}，文件数={len(self.all_files)}")

        self.filtered_files = list(self.all_files)
        self._fill_tree(self.find_tree, [(p,) for p in self.filtered_files])
        self.find_status.configure(text=f"查找结果：{len(self.filtered_files)}")

    def add_filter_box(self):
        row = ttk.Frame(self.filters_holder); row.pack(fill="x", pady=2)
        e = ttk.Entry(row, width=32); e.pack(side="left", padx=4)
        ttk.Label(row, text="（文件名需包含该子串 / 严格模式下取首条）").pack(side="left")
        self.filter_entries.append(e)

    def clear_filters(self):
        for e in self.filter_entries:
            e.master.destroy()
        self.filter_entries.clear()
        self.add_filter_box()
        self.filtered_files = list(self.all_files)
        self._fill_tree(self.find_tree, [(p,) for p in self.filtered_files])
        self.find_status.configure(text=f"查找结果：{len(self.filtered_files)}")
        self.log("[筛选清空]")

    def apply_filters(self):
        if not self.all_files:
            messagebox.showinfo("提示", "请先“展示文件”完成扫描。"); return
        tokens = [e.get() for e in self.filter_entries if e.get() != ""]
        strict = self.strict_exact.get()
        match_exts = normalize_ext_list(self.ext_text.get())

        if strict:
            if not tokens:
                res = []
            else:
                key = tokens[0]
                res = []
                for p in self.all_files:
                    _, base, _ = split_ext_composite(p, match_exts)
                    if base == key:  # 严格等于（大小写敏感）
                        res.append(p)
            mode_desc = f"严格匹配：'{tokens[0] if tokens else ''}'（不含扩展名）"
        else:
            res = []
            for p in self.all_files:
                name = os.path.basename(p)
                ok = all(t in name for t in tokens)  # AND 关系
                if ok:
                    res.append(p)
            mode_desc = f"包含（AND），关键字={tokens}"

        self.filtered_files = res
        self._fill_tree(self.find_tree, [(p,) for p in res])
        self.find_status.configure(text=f"查找结果：{len(res)}，{mode_desc}")
        self.log(f"[查找完成] {mode_desc}，命中={len(res)}")

    def add_replace_row(self):
        row = ttk.Frame(self.repl_holder); row.pack(fill="x", pady=2)
        old_e = ttk.Entry(row, width=24); new_e = ttk.Entry(row, width=24)
        ttk.Label(row, text="原：").pack(side="left"); old_e.pack(side="left", padx=2)
        ttk.Label(row, text="→ 新：").pack(side="left"); new_e.pack(side="left", padx=2)
        self.replace_rows.append((old_e, new_e))

    def clear_replace_rows(self):
        for old_e, new_e in self.replace_rows:
            old_e.master.destroy()
        self.replace_rows.clear()
        self.add_replace_row()

    def clear_preview(self):
        self.preview_pairs.clear()
        self._fill_tree(self.prev_tree, [])
        self.proc_status.configure(text="")
        self.log("[预览清空]")

    def build_preview(self):
        if not self.filtered_files:
            messagebox.showinfo("提示", "查找结果为空，请先完成“展示文件”和“查找”。")
            return

        match_exts = normalize_ext_list(self.ext_text.get())
        if not match_exts:
            messagebox.showwarning("提示", "请先输入用于匹配的扩展名。")
            return

        # 目标扩展
        if self.change_ext_enabled.get():
            ne = self.new_ext_text.get().strip()
            if not ne:
                messagebox.showwarning("提示", "已勾选“转换扩展名”，但未输入新扩展名。"); return
            new_exts = normalize_ext_list(ne)
            if len(new_exts) != 1:
                messagebox.showwarning("提示", "新扩展名只允许填写一个，例如 .nii.gz"); return
            new_ext = new_exts[0]
        else:
            new_ext = None

        add_cfg = (self.add_enabled.get(), self._to_int(self.add_pos.get(), 0), self.add_token.get())
        del_cfg = (self.del_enabled.get(), self._to_int(self.del_start.get(), 0), max(0, self._to_int(self.del_len.get(), 0)))

        repl_list = []
        if self.repl_enabled.get():
            for old_e, new_e in self.replace_rows:
                o, n = old_e.get(), new_e.get()
                if o == "" and n == "":
                    continue
                repl_list.append((o, n))

        pairs, changed_cnt = [], 0
        for old_path in self.filtered_files:
            d, base, ext = split_ext_composite(old_path, match_exts)
            new_base = apply_rename_rules(base, add_cfg, del_cfg, repl_list)
            new_ext_final = (new_ext if new_ext is not None else ext)
            if not new_ext_final.startswith("."):
                new_ext_final = "." + new_ext_final
            new_name = new_base + new_ext_final
            new_path = os.path.join(d, new_name)
            pairs.append((old_path, new_path))
            if new_path != old_path:
                changed_cnt += 1

        self.preview_pairs = pairs
        self._fill_tree(self.prev_tree, pairs)
        self.proc_status.configure(text=f"预览完成：{len(pairs)} 项，其中将改变 {changed_cnt} 项")
        self.log(f"[预览完成] 总计={len(pairs)}，改变={changed_cnt}")

        has_conflict, msgs = detect_conflicts(pairs)
        if has_conflict:
            self.log("检测到潜在冲突：\n" + "\n".join(msgs))
            messagebox.showwarning("警告", "检测到潜在命名冲突，详情见日志。请调整规则后再试。")

    def do_process(self):
        if not self.preview_pairs:
            self.build_preview()
            if not self.preview_pairs:
                return

        has_conflict, msgs = detect_conflicts(self.preview_pairs)
        if has_conflict:
            self.log("检测到冲突，已中止：\n" + "\n".join(msgs))
            messagebox.showerror("错误", "存在命名冲突，已中止。请先修正规则。")
            return

        success = 0; skip_same = 0; errors = 0
        conv_success = 0; ren_success = 0

        for old_p, new_p in self.preview_pairs:
            try:
                if old_p == new_p:
                    skip_same += 1; continue

                # 是否需要“真正转换”
                need_convert = False
                if self.change_ext_enabled.get() and self.convert_mode_enabled.get():
                    need_convert = True

                # 用户可选：当新旧均为 NIfTI（.nii 或 .nii.gz）时跳过
                if need_convert and self.skip_if_same_dtype_ext.get():
                    def _is_nifti(x):
                        lx = x.lower()
                        return lx.endswith('.nii') or lx.endswith('.nii.gz')
                    if _is_nifti(old_p) and _is_nifti(new_p):
                        self.log(f"[SKIP 同为NIfTI] {old_p}")
                        skip_same += 1
                        continue

                if need_convert:
                    if not SITK_AVAILABLE:
                        raise RuntimeError("未安装 SimpleITK：请先 pip install SimpleITK")
                    ensure_parent_dir(new_p)
                    convert_image_format(old_p, new_p)
                    conv_success += 1
                    success += 1
                    self.log(f"[CONVERT] {old_p}  =>  {new_p}")
                    if self.delete_source_after_convert.get():
                        try:
                            os.remove(old_p)
                            self.log(f"[DEL SRC] {old_p}")
                        except Exception as ie:
                            self.log(f"[WARN] 删除源失败 {old_p} :: {ie}")
                else:
                    # 仅改名/移动（不改格式）
                    ensure_parent_dir(new_p)
                    os.rename(old_p, new_p)
                    ren_success += 1
                    success += 1
                    self.log(f"[RENAME] {old_p}  ->  {new_p}")

            except Exception as e:
                errors += 1
                self.log(f"[ERR] {old_p} -> {new_p}  :: {e}")

        msg = (f"完成：成功 {success}（转换 {conv_success} / 重命名 {ren_success}），"
               f"跳过 {skip_same}，错误 {errors}")
        self.proc_status.configure(text=msg)
        messagebox.showinfo("完成", msg)

        # 刷新一遍界面
        self.scan_files(); self.apply_filters(); self.build_preview()

    def _fill_tree(self, tree, rows):
        tree.delete(*tree.get_children())
        if tree == self.prev_tree:
            for old_p, new_p in rows:
                tree.insert("", "end", values=(old_p, new_p))
        else:
            for (p,) in rows:
                tree.insert("", "end", values=(p,))

    def log(self, text):
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    @staticmethod
    def _to_int(s, default=0):
        try:
            return int(s.strip())
        except Exception:
            return default


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    app = RenameApp()
    app.mainloop()


if __name__ == "__main__":
    main()
