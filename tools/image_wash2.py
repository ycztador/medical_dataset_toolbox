

import os
import sys
import re
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ------------------------------
# 可选依赖：SimpleITK（真正格式转换用）
# ------------------------------
_sitk = None
try:
    import SimpleITK as sitk
    _sitk = sitk
except Exception:
    _sitk = None


# ------------------------------
# 工具函数：复合扩展名处理（.nii.gz）
# ------------------------------
def split_compound_ext(filename: str):
    """
    返回 (dirpath, basename_without_ext, ext) ，其中 ext 保留 .nii.gz 等复合后缀
    """
    dirpath = os.path.dirname(filename)
    base = os.path.basename(filename)
    # 优先匹配 .nii.gz
    if base.lower().endswith(".nii.gz"):
        name = base[:-7]
        ext = ".nii.gz"
    else:
        name, ext = os.path.splitext(base)
    return dirpath, name, ext


def has_allowed_ext(filepath: str, allowed_exts_set):
    # 与 split_compound_ext 配套：优先识别 .nii.gz
    _dir, _name, ext = split_compound_ext(filepath)
    return ext.lower() in allowed_exts_set


def norm_ext_list(ext_text: str):
    """
    ".nii,.nii.gz,.mha" → set{".nii",".nii.gz",".mha"}
    去掉空白，统一小写
    """
    items = [e.strip().lower() for e in re.split(r"[;,，\s]+", ext_text) if e.strip()]
    # 强制添加点号
    norm = set()
    for e in items:
        if not e.startswith("."):
            e = "." + e
        norm.add(e)
    return norm


# ------------------------------
# 可复用滚动容器
# ------------------------------
class ScrollableFrame(ttk.Frame):
    """纵向整窗滚动容器：将 UI 放入 self.content"""
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")

        self.content = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 鼠标滚轮（Win/mac）
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        # Linux/X11
        self.canvas.bind_all("<Button-4>", lambda e: self._on_button_scroll(-1), add="+")
        self.canvas.bind_all("<Button-5>", lambda e: self._on_button_scroll(1), add="+")
        self.content.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+"))
        self.content.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_frame_configure(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_mousewheel(self, event):
        delta = event.delta
        if delta == 0:
            return
        step = -1 if delta > 0 else 1
        if abs(delta) >= 120:
            step *= abs(delta) // 120
        self.canvas.yview_scroll(int(step), "units")

    def _on_button_scroll(self, direction):
        self.canvas.yview_scroll(direction, "units")


# ------------------------------
# 主应用
# ------------------------------
class RenameApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("课题组影像数据批量重命名/转存（支持 .nii.gz 等复合扩展 | 真正格式转换）")
        self.minsize(860, 600)

        # 变量区
        self.root_dir = tk.StringVar()
        self.ext_text = tk.StringVar(value=".nii,.nii.gz,.mha,.nrrd")
        self.all_files = []            # 扫描的文件全集
        self.filtered_files = []       # 筛选后的集合
        self.preview_pairs = []        # [(src, dst), ...]

        self.filter_entries = []       # 匹配关键字输入框
        self.replace_rows = []         # [(old_var, new_var, row_frame), ...]

        self.add_enabled = tk.BooleanVar(value=False)
        self.del_enabled = tk.BooleanVar(value=False)
        self.repl_enabled = tk.BooleanVar(value=False)
        self.change_ext_enabled = tk.BooleanVar(value=True)
        self.strict_exact = tk.BooleanVar(value=False)

        self.convert_mode_enabled = tk.BooleanVar(value=True)  # True=真正格式转换；False=仅改后缀
        self.delete_source_after_convert = tk.BooleanVar(value=False)
        self.skip_if_same_dtype_ext = tk.BooleanVar(value=False)

        self.add_pos = tk.StringVar(value="0")
        self.add_token = tk.StringVar(value="")
        self.del_start = tk.StringVar(value="0")
        self.del_len = tk.StringVar(value="1")
        self.new_ext_text = tk.StringVar(value=".nii.gz")

        # 外层滚动容器
        shell = ScrollableFrame(self)
        shell.pack(fill="both", expand=True)

        # 构建 UI 到 shell.content
        self._build_ui(parent=shell.content)

    # ------------------ UI ------------------
    def _build_ui(self, parent):
        pad = {"padx": 8, "pady": 6}

        # 顶部：选择根目录
        top = ttk.Frame(parent); top.pack(fill="x", **pad)
        ttk.Label(top, text="根目录：").pack(side="left")
        ttk.Entry(top, textvariable=self.root_dir, width=62).pack(side="left", padx=4)
        ttk.Button(top, text="选择...", command=self.choose_root).pack(side="left", padx=4)
        ttk.Button(top, text="扫描影像文件", command=self.scan_files).pack(side="left", padx=4)

        # 扩展名设置
        extf = ttk.Frame(parent); extf.pack(fill="x", **pad)
        ttk.Label(extf, text="匹配扩展：").pack(side="left")
        ttk.Entry(extf, textvariable=self.ext_text, width=40).pack(side="left", padx=4)
        ttk.Label(extf, text="（用 , ; 或空格分隔；支持 .nii.gz）").pack(side="left")

        # 全部文件列表
        listf = ttk.LabelFrame(parent, text="匹配扩展名的文件（全部）")
        listf.pack(fill="both", expand=True, **pad)
        self.tree_all = ttk.Treeview(listf, columns=("path",), show="headings", height=8)
        self.tree_all.heading("path", text="File Path")
        self.tree_all.column("path", width=900, anchor="w")
        self.tree_all.pack(fill="both", expand=True)

        # 筛选区
        filtf = ttk.LabelFrame(parent, text="匹配筛选")
        filtf.pack(fill="x", **pad)

        line1 = ttk.Frame(filtf); line1.pack(fill="x", pady=2)
        ttk.Label(line1, text="关键字：").pack(side="left")
        self.filter_box = ttk.Frame(line1); self.filter_box.pack(side="left", padx=6)
        self.add_filter_entry()  # 默认一条
        ttk.Button(line1, text="+", width=3, command=self.add_filter_entry).pack(side="left")
        ttk.Button(line1, text="-", width=3, command=self.remove_filter_entry).pack(side="left")
        ttk.Checkbutton(line1, text="当且仅当（严格匹配）", variable=self.strict_exact).pack(side="left", padx=8)
        ttk.Button(line1, text="筛选预览", command=self.apply_filters).pack(side="right")

        self.tree_filtered = ttk.Treeview(filtf, columns=("path",), show="headings", height=7)
        self.tree_filtered.heading("path", text="Filtered File Path")
        self.tree_filtered.column("path", width=900, anchor="w")
        self.tree_filtered.pack(fill="x", padx=2, pady=4)

        # 规则设置
        rulef = ttk.LabelFrame(parent, text="规则设置")
        rulef.pack(fill="x", **pad)

        # 增加
        addf = ttk.Frame(rulef); addf.pack(fill="x", pady=2)
        ttk.Checkbutton(addf, text="增加", variable=self.add_enabled).pack(side="left")
        ttk.Label(addf, text="位置 index：").pack(side="left", padx=4)
        ttk.Entry(addf, textvariable=self.add_pos, width=6).pack(side="left")
        ttk.Label(addf, text="插入字符串：").pack(side="left", padx=6)
        ttk.Entry(addf, textvariable=self.add_token, width=30).pack(side="left", padx=2)

        # 删除
        delf = ttk.Frame(rulef); delf.pack(fill="x", pady=2)
        ttk.Checkbutton(delf, text="减少（删除）", variable=self.del_enabled).pack(side="left")
        ttk.Label(delf, text="起始 index：").pack(side="left", padx=4)
        ttk.Entry(delf, textvariable=self.del_start, width=6).pack(side="left")
        ttk.Label(delf, text="长度：").pack(side="left", padx=4)
        ttk.Entry(delf, textvariable=self.del_len, width=6).pack(side="left")

        # 替换
        replf = ttk.Frame(rulef); replf.pack(fill="x", pady=2)
        ttk.Checkbutton(replf, text="替换", variable=self.repl_enabled).pack(side="left")
        ttk.Button(replf, text="添加替换对", command=self.add_replace_row).pack(side="left", padx=6)
        ttk.Button(replf, text="删除最后一对", command=self.remove_replace_row).pack(side="left")
        self.repl_box = ttk.Frame(rulef); self.repl_box.pack(fill="x", padx=2, pady=2)

        # 扩展名与转换方式
        extset = ttk.Frame(rulef); extset.pack(fill="x", pady=4)
        ttk.Checkbutton(extset, text="转换扩展名", variable=self.change_ext_enabled).pack(side="left")
        ttk.Entry(extset, textvariable=self.new_ext_text, width=12).pack(side="left", padx=4)
        ttk.Label(extset, text="模式：").pack(side="left", padx=12)
        ttk.Radiobutton(extset, text="真正格式转换（SimpleITK）", value=True,
                        variable=self.convert_mode_enabled).pack(side="left")
        ttk.Radiobutton(extset, text="仅改后缀（不改文件内容）", value=False,
                        variable=self.convert_mode_enabled).pack(side="left", padx=8)
        ttk.Checkbutton(extset, text="转换后删除源文件", variable=self.delete_source_after_convert).pack(side="left", padx=12)
        ttk.Checkbutton(extset, text="同类型同后缀时跳过", variable=self.skip_if_same_dtype_ext).pack(side="left", padx=8)

        # 操作
        actf = ttk.Frame(parent); actf.pack(fill="x", **pad)
        ttk.Button(actf, text="生成转换预览", command=self.build_preview).pack(side="left")
        ttk.Button(actf, text="执行改名/转换", command=self.execute_apply).pack(side="left", padx=8)

        # 预览
        prevf = ttk.LabelFrame(parent, text="转换预览（Original → New）")
        prevf.pack(fill="both", expand=True, **pad)
        self.tree_prev = ttk.Treeview(prevf, columns=("src", "dst"), show="headings", height=9)
        self.tree_prev.heading("src", text="Original")
        self.tree_prev.heading("dst", text="New")
        self.tree_prev.column("src", width=520, anchor="w")
        self.tree_prev.column("dst", width=520, anchor="w")
        self.tree_prev.pack(fill="both", expand=True)

        # 日志
        logf = ttk.LabelFrame(parent, text="日志")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=8)
        self.log.pack(fill="both", expand=True)

    # ------------------ 逻辑 ------------------
    def choose_root(self):
        d = filedialog.askdirectory(title="选择根目录")
        if d:
            self.root_dir.set(d)

    def scan_files(self):
        root = self.root_dir.get().strip()
        if not root:
            messagebox.showwarning("提示", "请先选择根目录")
            return
        exts = norm_ext_list(self.ext_text.get())
        matched = []
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                full = os.path.join(dirpath, f)
                if has_allowed_ext(full, exts):
                    matched.append(os.path.normpath(full))
        self.all_files = sorted(matched)
        self._refresh_tree(self.tree_all, [(p,) for p in self.all_files])
        self._log(f"[SCAN] 共发现 {len(self.all_files)} 个匹配扩展的文件。")

    def add_filter_entry(self):
        row = ttk.Frame(self.filter_box)
        var = tk.StringVar(value="")
        ent = ttk.Entry(row, textvariable=var, width=28)
        ttk.Label(row, text="包含：").pack(side="left", padx=(0, 4))
        ent.pack(side="left")
        row.pack(side="left", padx=2)
        self.filter_entries.append((var, row))

    def remove_filter_entry(self):
        if not self.filter_entries:
            return
        var, row = self.filter_entries.pop()
        row.destroy()

    def apply_filters(self):
        if not self.all_files:
            self._log("[FILTER] 尚未扫描文件。")
            return
        keys = [v.get().strip() for v, _ in self.filter_entries if v.get().strip()]
        if not keys:
            # 没有关键字就等于不过滤
            self.filtered_files = list(self.all_files)
        else:
            res = []
            for p in self.all_files:
                name = os.path.basename(p)
                if self.strict_exact.get():
                    # 当且仅当：文件名必须“恰好等于其中一个关键字”
                    if name in keys:
                        res.append(p)
                else:
                    # 一般包含：全部关键字都在文件全路径里出现
                    if all(k.lower() in p.lower() for k in keys):
                        res.append(p)
            self.filtered_files = res
        self._refresh_tree(self.tree_filtered, [(p,) for p in self.filtered_files])
        self._log(f"[FILTER] 关键字={keys} 严格={self.strict_exact.get()} → 命中 {len(self.filtered_files)} 个。")

    def add_replace_row(self):
        row = ttk.Frame(self.repl_box)
        old_var = tk.StringVar(value="")
        new_var = tk.StringVar(value="")
        ttk.Label(row, text="原：").pack(side="left")
        ttk.Entry(row, textvariable=old_var, width=20).pack(side="left", padx=4)
        ttk.Label(row, text="→ 新：").pack(side="left")
        ttk.Entry(row, textvariable=new_var, width=20).pack(side="left", padx=4)
        row.pack(fill="x", pady=2)
        self.replace_rows.append((old_var, new_var, row))

    def remove_replace_row(self):
        if not self.replace_rows:
            return
        old_var, new_var, row = self.replace_rows.pop()
        row.destroy()

    def build_preview(self):
        files = self.filtered_files if self.filtered_files else self.all_files
        if not files:
            self._log("[PREVIEW] 没有可预览的文件，请先扫描/筛选。")
            return

        add_en = self.add_enabled.get()
        del_en = self.del_enabled.get()
        repl_en = self.repl_enabled.get()
        chg_ext = self.change_ext_enabled.get()
        convmode = self.convert_mode_enabled.get()
        newext = self.new_ext_text.get().strip()

        try:
            add_pos = int(self.add_pos.get().strip() or "0")
        except ValueError:
            add_pos = 0
        add_token = self.add_token.get()

        try:
            del_start = int(self.del_start.get().strip() or "0")
        except ValueError:
            del_start = 0
        try:
            del_len = int(self.del_len.get().strip() or "0")
        except ValueError:
            del_len = 0

        repl_pairs = [(ov.get(), nv.get()) for ov, nv, _ in self.replace_rows if ov.get()]

        pairs = []
        for src in files:
            d, base, ext = split_compound_ext(src)
            name = base  # 初始为纯文件名（不含扩展）
            # 替换
            if repl_en and repl_pairs:
                for old, new in repl_pairs:
                    name = name.replace(old, new)
            # 删除
            if del_en and del_len > 0:
                try:
                    name = name[:del_start] + name[del_start+del_len:]
                except Exception:
                    pass
            # 增加
            if add_en and add_token:
                try:
                    if add_pos < 0:
                        idx = len(name) + add_pos
                    else:
                        idx = add_pos
                    idx = max(0, min(len(name), idx))
                    name = name[:idx] + add_token + name[idx:]
                except Exception:
                    pass
            # 扩展名
            out_ext = ext
            if chg_ext and newext:
                ne = newext if newext.startswith(".") else ("." + newext)
                out_ext = ne

            dst = os.path.join(d, name + out_ext)
            pairs.append((src, os.path.normpath(dst)))

        self.preview_pairs = pairs
        self._refresh_tree(self.tree_prev, pairs)
        self._log(f"[PREVIEW] 生成 {len(pairs)} 条 Original → New。模式={'转换' if convmode else '仅改后缀'}；新扩展={newext if chg_ext else '(不变)'}")

    def execute_apply(self):
        if not self.preview_pairs:
            self._log("[APPLY] 先生成转换预览。")
            return

        convmode = self.convert_mode_enabled.get()
        delete_src = self.delete_source_after_convert.get()
        skip_same = self.skip_if_same_dtype_ext.get()

        cnt_ok, cnt_skip, cnt_err = 0, 0, 0
        for src, dst in self.preview_pairs:
            try:
                if os.path.normpath(src) == os.path.normpath(dst):
                    self._log(f"[SKIP] 相同路径：{src}")
                    cnt_skip += 1
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)

                if convmode:
                    # 真正格式转换
                    if _sitk is None:
                        self._log("[ERROR] 未安装 SimpleITK，无法进行真正格式转换。请先 pip install SimpleITK。")
                        cnt_err += 1
                        continue
                    # 如果用户勾选“同类型同后缀时跳过”
                    if skip_same:
                        _d1, _b1, e1 = split_compound_ext(src)
                        _d2, _b2, e2 = split_compound_ext(dst)
                        if e1.lower() == e2.lower():
                            self._log(f"[SKIP] 同扩展跳过（{e1}）：{src}")
                            cnt_skip += 1
                            continue
                    # 读写
                    img = _sitk.ReadImage(src)
                    _sitk.WriteImage(img, dst)
                    self._log(f"[OK] 转换写出：{dst}")
                    cnt_ok += 1
                    if delete_src:
                        try:
                            os.remove(src)
                            self._log(f"      已删除源文件：{src}")
                        except Exception as e:
                            self._log(f"      [WARN] 删除源失败：{e}")
                else:
                    # 仅改后缀/重命名
                    if skip_same:
                        _d1, _b1, e1 = split_compound_ext(src)
                        _d2, _b2, e2 = split_compound_ext(dst)
                        if e1.lower() == e2.lower() and os.path.basename(src) == os.path.basename(dst):
                            self._log(f"[SKIP] 名称未变化：{src}")
                            cnt_skip += 1
                            continue
                    os.replace(src, dst)
                    self._log(f"[OK] 重命名：{src} → {dst}")
                    cnt_ok += 1
                self.update_idletasks()
            except Exception as e:
                self._log(f"[ERR] {src} -> {dst} : {e}")
                cnt_err += 1

        self._log(f"[DONE] 成功={cnt_ok} 跳过={cnt_skip} 失败={cnt_err}")

    # ------------------ 小工具 ------------------
    def _refresh_tree(self, tree: ttk.Treeview, rows):
        for i in tree.get_children():
            tree.delete(i)
        for row in rows:
            tree.insert("", "end", values=row)

    def _log(self, msg: str):
        self.log.insert("end", msg + "\n")
        self.log.see("end")


if __name__ == "__main__":
    app = RenameApp()
    app.mainloop()
