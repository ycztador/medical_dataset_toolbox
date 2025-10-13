import os
import sys
import shutil
import random
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "NII按CASE整理工具（root扫描 / 位置切片匹配 / 预览与复制）"

def is_nii_gz(p: Path) -> bool:
    return p.name.lower().endswith(".nii.gz")

def walk_nii_gz(root: Path):
    """返回 (path, is_deep) 列表；is_deep 表示是否为root下多级目录文件"""
    res = []
    root = root.resolve()
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".nii.gz"):
                p = Path(dirpath) / fn
                # 相对 root 的层级是否>1（即不在 root 的第一层）
                rel = p.parent.relative_to(root)
                is_deep = len(rel.parts) > 0  # 只要不在root根目录，就算“深层”
                res.append((p, is_deep))
    return res

def ensure_unique(dst_path: Path) -> Path:
    """若文件已存在，自动追加 _1, _2,... 直到唯一"""
    if not dst_path.exists():
        return dst_path
    stem = dst_path.name[:-7] if dst_path.name.lower().endswith(".nii.gz") else dst_path.stem
    suffix = ".nii.gz" if dst_path.name.lower().endswith(".nii.gz") else dst_path.suffix
    parent = dst_path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1

def safe_copy_file(src: Path, dst: Path, overwrite: bool):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if overwrite:
            shutil.copy2(src, dst)
            return dst
        else:
            dst = ensure_unique(dst)
            shutil.copy2(src, dst)
            return dst
    else:
        shutil.copy2(src, dst)
        return dst

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x720")
        self.minsize(1000, 680)

        self.root_dir = tk.StringVar()
        self.dst_dir = tk.StringVar()
        self.slice_start = tk.IntVar(value=0)
        self.slice_end = tk.IntVar(value=0)
        self.snippet = tk.StringVar()
        self.flatten_deep = tk.BooleanVar(value=False)  # 统一转移至root
        self.overwrite = tk.BooleanVar(value=False)     # 目标重复时是否覆盖
        self.scan_results = []  # [(Path, is_deep)]
        self.group_map = {}     # {ID: [Path,...]}
        self.example_path = None

        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)

        # row1: root / dst 选择
        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X, pady=(0,6))
        ttk.Label(row1, text="root：").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.root_dir, width=70).pack(side=tk.LEFT, padx=4)
        ttk.Button(row1, text="浏览root", command=self.browse_root).pack(side=tk.LEFT, padx=2)
        ttk.Label(row1, text="   dst：").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.dst_dir, width=40).pack(side=tk.LEFT, padx=4)
        ttk.Button(row1, text="浏览dst", command=self.browse_dst).pack(side=tk.LEFT, padx=2)

        # row2: 操作与选项
        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X)
        ttk.Button(row2, text="扫描 .nii.gz", command=self.scan_files).pack(side=tk.LEFT)
        ttk.Label(row2, text="  ").pack(side=tk.LEFT)
        ttk.Checkbutton(row2, text="将“深层”文件统一拷贝到root（先扁平，再整理）", variable=self.flatten_deep).pack(side=tk.LEFT)
        ttk.Label(row2, text="  ").pack(side=tk.LEFT)
        ttk.Checkbutton(row2, text="复制时若重名则覆盖（不勾选=自动加 _1）", variable=self.overwrite).pack(side=tk.LEFT)

        # 主体分割
        paned = ttk.Panedwindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 上半：扫描结果（含深层标记）
        scan_frame = ttk.Labelframe(paned, text="扫描结果（[深层] 表示不在 root 第一层）")
        paned.add(scan_frame, weight=1)

        self.scan_list = tk.Listbox(scan_frame, height=10)
        yscroll = ttk.Scrollbar(scan_frame, orient=tk.VERTICAL, command=self.scan_list.yview)
        self.scan_list.config(yscrollcommand=yscroll.set)
        self.scan_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6,0), pady=6)
        yscroll.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0,6))

        # 中部：示例 & 匹配切片
        match_box = ttk.Labelframe(paned, text="ID 匹配规则（基于文件名切片）")
        paned.add(match_box, weight=0)

        # 示例路径
        ex_row = ttk.Frame(match_box)
        ex_row.pack(fill=tk.X, padx=8, pady=(8,4))
        ttk.Button(ex_row, text="随机示例", command=self.pick_example).pack(side=tk.LEFT)
        self.example_label = ttk.Label(ex_row, text="（示例路径将显示在这里）", foreground="#444")
        self.example_label.pack(side=tk.LEFT, padx=8)

        # snippet -> 计算 [start:end]
        s_row = ttk.Frame(match_box)
        s_row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(s_row, text="在示例文件名中输入片段：").pack(side=tk.LEFT)
        ttk.Entry(s_row, textvariable=self.snippet, width=30).pack(side=tk.LEFT, padx=6)
        ttk.Button(s_row, text="定位切片区间", command=self.locate_slice_by_snippet).pack(side=tk.LEFT, padx=4)
        ttk.Label(s_row, text=" 或手动设置下方切片起止：").pack(side=tk.LEFT)

        # 切片数值
        idx_row = ttk.Frame(match_box)
        idx_row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(idx_row, text="start：").pack(side=tk.LEFT)
        ttk.Spinbox(idx_row, from_=0, to=9999, textvariable=self.slice_start, width=8).pack(side=tk.LEFT)
        ttk.Label(idx_row, text="   end：").pack(side=tk.LEFT)
        ttk.Spinbox(idx_row, from_=0, to=9999, textvariable=self.slice_end, width=8).pack(side=tk.LEFT)
        ttk.Button(idx_row, text="提取并分组（按切片作为ID）", command=self.group_by_slice).pack(side=tk.LEFT, padx=10)

        self.slice_hint = ttk.Label(match_box, text="提示：切片作用于“文件名（不含扩展名）”的字符区间 [start:end)。", foreground="#666")
        self.slice_hint.pack(anchor="w", padx=10, pady=(0,8))

        # 下半：预览表 + 执行区
        bottom = ttk.Labelframe(paned, text="预览与执行")
        paned.add(bottom, weight=1)

        # 预览表（dst / ID / nii.gz 三列）
        cols = ("dst", "id", "file")
        self.tree = ttk.Treeview(bottom, columns=cols, show="headings")
        self.tree.heading("dst", text="dst（仅首行显示）")
        self.tree.heading("id", text="ID（仅首行显示）")
        self.tree.heading("file", text="nii.gz 文件名")
        self.tree.column("dst", width=320, anchor="w")
        self.tree.column("id", width=200, anchor="w")
        self.tree.column("file", width=440, anchor="w")
        y2 = ttk.Scrollbar(bottom, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=y2.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6,0), pady=6)
        y2.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0,6))

        # 执行区（按钮+进度+日志）
        exec_box = ttk.Frame(bottom)
        exec_box.pack(side=tk.RIGHT, fill=tk.Y, padx=6, pady=6)

        ttk.Button(exec_box, text="预览导出结构", command=self.preview_structure).pack(fill=tk.X, pady=(0,6))
        ttk.Button(exec_box, text="确认开始复制", command=self.start_execute).pack(fill=tk.X)

        ttk.Label(exec_box, text="进度：").pack(anchor="w", pady=(12,0))
        self.pg = ttk.Progressbar(exec_box, orient=tk.HORIZONTAL, mode="determinate", length=220, maximum=100)
        self.pg.pack(pady=(2,6))

        ttk.Label(exec_box, text="日志：").pack(anchor="w")
        self.log = tk.Text(exec_box, height=18, width=40)
        self.log.pack(fill=tk.BOTH, expand=True)

    def browse_root(self):
        d = filedialog.askdirectory(title="选择 root")
        if d:
            self.root_dir.set(d)

    def browse_dst(self):
        d = filedialog.askdirectory(title="选择 dst")
        if d:
            self.dst_dir.set(d)

    def append_scan_list(self):
        self.scan_list.delete(0, tk.END)
        for p, deep in self.scan_results:
            tag = "[深层] " if deep else ""
            self.scan_list.insert(tk.END, f"{tag}{str(p)}")
        self.scan_list.insert(tk.END, f"—— 共计 {len(self.scan_results)} 个 .nii.gz ——")

    def scan_files(self):
        root = self.root_dir.get().strip()
        if not root:
            messagebox.showwarning("提示", "请先选择 root 目录。")
            return
        rootp = Path(root)
        if not rootp.exists():
            messagebox.showerror("错误", "root 路径不存在。")
            return

        self.scan_results = walk_nii_gz(rootp)
        self.append_scan_list()
        self.example_path = None
        self.example_label.config(text="（示例路径将显示在这里）")
        self.group_map.clear()
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.log_delete()
        self.log_write(f"[INFO] 扫描完成：共 {len(self.scan_results)} 个 .nii.gz；其中深层：{sum(1 for _,d in self.scan_results if d)}\n")

        # 如用户勾选“统一转移至root”，此处仅询问（真正执行在“确认开始复制”时）
        if any(d for _, d in self.scan_results):
            if self.flatten_deep.get():
                self.log_write("[HINT] 已勾选：稍后会先将深层文件统一拷贝到 root 再进行整理。\n")
            else:
                self.log_write("[HINT] 检测到深层文件，如需先扁平化，请勾选“将‘深层’文件统一拷贝到root”。\n")

    def pick_example(self):
        if not self.scan_results:
            messagebox.showwarning("提示", "请先扫描 .nii.gz。")
            return
        self.example_path = random.choice(self.scan_results)[0]
        self.example_label.config(text=str(self.example_path))

    def locate_slice_by_snippet(self):
        if not self.example_path:
            messagebox.showwarning("提示", "请先点“随机示例”。")
            return
        snip = self.snippet.get().strip()
        if not snip:
            messagebox.showwarning("提示", "请先输入片段（例如 Breast_001）。")
            return
        name_no_ext = self.example_path.name[:-7] if self.example_path.name.lower().endswith(".nii.gz") else self.example_path.stem
        idx = name_no_ext.find(snip)
        if idx < 0:
            messagebox.showerror("未找到", f"片段“{snip}”未在示例文件名中找到：\n{name_no_ext}")
            return
        start = idx
        end = idx + len(snip)
        self.slice_start.set(start)
        self.slice_end.set(end)
        self.log_write(f"[OK] 依据片段定位切片区间：[start:end) = [{start}:{end})\n")
        self.log_write(f"     示例文件名：{name_no_ext}\n")
        self.log_write(f"     示例提取ID：{name_no_ext[start:end]}\n")

    def group_by_slice(self):
        if not self.scan_results:
            messagebox.showwarning("提示", "请先扫描 .nii.gz。")
            return
        s = self.slice_start.get()
        e = self.slice_end.get()
        if e <= s:
            messagebox.showerror("错误", "切片区间非法：end 必须大于 start。")
            return

        self.group_map.clear()
        bad = 0
        for p, _ in self.scan_results:
            name_no_ext = p.name[:-7] if p.name.lower().endswith(".nii.gz") else p.stem
            if s >= len(name_no_ext):
                bad += 1
                continue
            ee = min(e, len(name_no_ext))
            _id = name_no_ext[s:ee]
            if not _id:
                bad += 1
                continue
            self.group_map.setdefault(_id, []).append(p)

        self.log_write(f"[INFO] 分组完成：共 {len(self.group_map)} 个ID；无法提取的文件 {bad} 个。\n")
        self.preview_structure()

    def preview_structure(self):
        # 清空预览树
        for i in self.tree.get_children():
            self.tree.delete(i)

        dst_root = self.dst_dir.get().strip()
        if not dst_root:
            messagebox.showwarning("提示", "请先选择 dst 目录。")
            return
        if not self.group_map:
            self.log_write("[WARN] 暂无分组结果。请先设置切片并“提取并分组”。\n")
            return

        # 预览插入：dst 与 ID 只在每组第一行展示
        total = 0
        for _id, paths in sorted(self.group_map.items()):
            show_dst = str(Path(dst_root).resolve())
            show_id = _id
            for i, p in enumerate(sorted(paths)):
                file_name = p.name
                self.tree.insert("", tk.END,
                                 values=(show_dst if i == 0 else "",
                                         show_id if i == 0 else "",
                                         file_name))
                total += 1
        self.log_write(f"[OK] 预览完成：{len(self.group_map)} 个ID，共 {total} 个文件。\n")

    def start_execute(self):
        dst_root = self.dst_dir.get().strip()
        root = self.root_dir.get().strip()
        if not root:
            messagebox.showwarning("提示", "请先选择 root 目录。")
            return
        if not dst_root:
            messagebox.showwarning("提示", "请先选择 dst 目录。")
            return
        if not self.group_map:
            messagebox.showwarning("提示", "请先完成分组并预览。")
            return
        if not messagebox.askokcancel("确认", "确认开始复制吗？"):
            return

        t = threading.Thread(target=self._execute_copy, daemon=True)
        t.start()

    def _execute_copy(self):
        try:
            self._set_progress(0)
            rootp = Path(self.root_dir.get().strip()).resolve()
            dst_rootp = Path(self.dst_dir.get().strip()).resolve()
            overwrite = self.overwrite.get()

            # Step 0（可选）：先把“深层”文件统一拷贝到 root
            if self.flatten_deep.get():
                deep_files = [(p, d) for p, d in self.scan_results if d]
                self.log_write(f"[STEP] 扁平化到 root：深层文件 {len(deep_files)} 个。\n")
                done = 0
                for p, _ in deep_files:
                    dstp = rootp / p.name
                    dstp = dstp if overwrite else ensure_unique(dstp)
                    shutil.copy2(p, dstp)
                    done += 1
                    if done % 10 == 0:
                        self._set_progress(5 + int(done * 5 / max(1, len(deep_files))))
                self.log_write("[OK] 扁平化完成。\n")
                # 扁平化后，建议重新以 root 扫描用于后续复制的“来源”
                self.scan_results = walk_nii_gz(rootp)

            # Step 1：按分组复制到 dst/ID/文件名
            all_files = sum(len(v) for v in self.group_map.values())
            self.log_write(f"[STEP] 开始复制：{len(self.group_map)} 个ID，共 {all_files} 个文件。\n")
            done = 0
            for _id, paths in sorted(self.group_map.items()):
                for src in sorted(paths):
                    dstp = dst_rootp / _id / src.name
                    copied_to = safe_copy_file(src, dstp, overwrite)
                    self.log_write(f"[COPY] {src}  ->  {copied_to}\n")
                    done += 1
                    prog = 10 + int(done * 90 / max(1, all_files))
                    self._set_progress(prog)

            self._set_progress(100)
            self.log_write("[DONE] 全部复制完成。\n")
            messagebox.showinfo("完成", "复制完成！")
        except Exception as e:
            self.log_write(f"[ERROR] {e}\n")
            messagebox.showerror("错误", str(e))

    # 日志 & 进度
    def log_write(self, s: str):
        self.log.insert(tk.END, s)
        self.log.see(tk.END)
        self.update_idletasks()

    def log_delete(self):
        self.log.delete("1.0", tk.END)

    def _set_progress(self, v: int):
        self.pg["value"] = max(0, min(100, v))
        self.update_idletasks()

if __name__ == "__main__":
    app = App()
    app.mainloop()
