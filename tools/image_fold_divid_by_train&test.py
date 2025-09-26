import os
import shutil
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# pandas 用于读 Excel
try:
    import pandas as pd
except Exception as e:
    raise RuntimeError("请先安装 pandas 与 openpyxl：pip install pandas openpyxl") from e


ALLOWED_SPLITS = {"train", "test", "validation"}  # 允许的划分标签（大小写不敏感）


def normalize_id(s: str) -> str:
    """宽松匹配时用：去首尾空格、压缩中间多空格为一个、转小写。"""
    if s is None:
        return ""
    # 统一空格：split再join
    return " ".join(str(s).strip().split()).lower()


class SplitGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("文件区分梳理工具（按Excel的train/test/validation划分）")
        self.geometry("1000x650")

        # 变量
        self.root_dir = tk.StringVar()
        self.dst_dir = tk.StringVar()
        self.excel_path = tk.StringVar()
        self.id_col = tk.StringVar()
        self.split_col = tk.StringVar()
        self.strict_match = tk.BooleanVar(value=True)
        self.copy_mode = tk.StringVar(value="copy")  # "copy" or "move"
        self.only_ids_in_excel = tk.BooleanVar(value=True)

        # 状态缓存
        self.case_dirs = {}           # case_id -> Path(root/case_id)
        self.df = None                # Excel DataFrame
        self.id_to_split = {}         # 解析后的唯一映射（无冲突）
        self.conflicts = {}           # 冲突映射 id -> set(splits)
        self.unknown_split_ids = set()# split值不在 ALLOWED_SPLITS
        self.not_found_ids = set()    # Excel中出现但在root未找到
        self.orphan_cases = set()     # root中出现但Excel未定义（仅预览时展示）
        self.plan = {}                # "train"/"test"/"validation" -> [case_id, ...]

        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = {'padx': 6, 'pady': 4}

        frm_top = ttk.LabelFrame(self, text="路径与参数")
        frm_top.pack(fill="x", padx=8, pady=8)

        # 行1：root / dst
        row1 = ttk.Frame(frm_top)
        row1.pack(fill="x", **pad)

        ttk.Label(row1, text="数据根目录 root：").grid(row=0, column=0, sticky="w")
        ttk.Entry(row1, textvariable=self.root_dir, width=60).grid(row=0, column=1, sticky="we")
        ttk.Button(row1, text="选择...", command=self.browse_root).grid(row=0, column=2, padx=4)

        ttk.Label(row1, text="输出目录 dst：").grid(row=1, column=0, sticky="w")
        ttk.Entry(row1, textvariable=self.dst_dir, width=60).grid(row=1, column=1, sticky="we")
        ttk.Button(row1, text="选择...", command=self.browse_dst).grid(row=1, column=2, padx=4)

        row1.grid_columnconfigure(1, weight=1)

        # 行2：Excel
        row2 = ttk.Frame(frm_top)
        row2.pack(fill="x", **pad)

        ttk.Label(row2, text="统计表 Excel：").grid(row=0, column=0, sticky="w")
        ttk.Entry(row2, textvariable=self.excel_path, width=60).grid(row=0, column=1, sticky="we")
        ttk.Button(row2, text="选择...", command=self.browse_excel).grid(row=0, column=2, padx=4)
        ttk.Button(row2, text="浏览Excel并统计", command=self.load_excel).grid(row=0, column=3, padx=8)

        row2.grid_columnconfigure(1, weight=1)

        # 行3：列名与选项
        row3 = ttk.Frame(frm_top)
        row3.pack(fill="x", **pad)

        ttk.Label(row3, text="ID 列名：").grid(row=0, column=0, sticky="w")
        ttk.Entry(row3, textvariable=self.id_col, width=20).grid(row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(row3, text="划分列名：").grid(row=0, column=2, sticky="w")
        ttk.Entry(row3, textvariable=self.split_col, width=20).grid(row=0, column=3, sticky="w", padx=(0, 16))

        ttk.Checkbutton(row3, text="严格匹配（区分大小写与空格）", variable=self.strict_match).grid(row=0, column=4, sticky="w")
        ttk.Checkbutton(row3, text="只处理表中出现的ID", variable=self.only_ids_in_excel).grid(row=0, column=5, sticky="w", padx=(16,0))

        # 行4：操作按钮
        row4 = ttk.Frame(frm_top)
        row4.pack(fill="x", **pad)

        ttk.Button(row4, text="扫描 root", command=self.scan_root).grid(row=0, column=0, padx=4)
        ttk.Button(row4, text="预览梳理", command=self.preview_plan).grid(row=0, column=1, padx=4)

        ttk.Radiobutton(row4, text="复制（推荐）", variable=self.copy_mode, value="copy").grid(row=0, column=2, padx=(24,4))
        ttk.Radiobutton(row4, text="移动", variable=self.copy_mode, value="move").grid(row=0, column=3, padx=4)

        ttk.Button(row4, text="开始梳理", command=self.execute_plan).grid(row=0, column=4, padx=24)

        # 进度条
        self.progress = ttk.Progressbar(frm_top, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", **pad)

        # 日志输出
        frm_log = ttk.LabelFrame(self, text="统计 / 日志")
        frm_log.pack(fill="both", expand=True, padx=8, pady=8)

        self.txt = tk.Text(frm_log, wrap="word", height=18)
        self.txt.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(frm_log, command=self.txt.yview)
        scroll.pack(side="right", fill="y")
        self.txt.configure(yscrollcommand=scroll.set)

    # --------------- 事件 ---------------
    def browse_root(self):
        d = filedialog.askdirectory(title="选择 root（包含多个 caseID 的目录）")
        if d:
            self.root_dir.set(d)

    def browse_dst(self):
        d = filedialog.askdirectory(title="选择输出目录 dst")
        if d:
            self.dst_dir.set(d)

    def browse_excel(self):
        f = filedialog.askopenfilename(title="选择统计表 Excel", filetypes=[("Excel", "*.xlsx *.xls")])
        if f:
            self.excel_path.set(f)

    def log(self, msg: str):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.update_idletasks()

    # 扫描 root 的第一层目录作为 caseID
    def scan_root(self):
        root = Path(self.root_dir.get().strip())
        if not root.is_dir():
            messagebox.showerror("错误", "请先选择有效的 root 目录")
            return
        self.case_dirs.clear()
        for p in root.iterdir():
            if p.is_dir():
                self.case_dirs[p.name] = p
        self.log(f"[INFO] root 下共发现 {len(self.case_dirs)} 个 caseID 目录。示例：{list(self.case_dirs.keys())[:5]}")

    def load_excel(self):
        excel = self.excel_path.get().strip()
        if not excel:
            messagebox.showerror("错误", "请先选择 Excel 文件")
            return
        id_col = self.id_col.get().strip()
        split_col = self.split_col.get().strip()
        if not id_col or not split_col:
            messagebox.showerror("错误", "请填写 ID 列名 与 划分列名")
            return
        try:
            self.df = pd.read_excel(excel)
        except Exception as e:
            messagebox.showerror("错误", f"读取 Excel 失败：{e}")
            return

        if id_col not in self.df.columns or split_col not in self.df.columns:
            messagebox.showerror("错误", f"Excel 中找不到列：{id_col} 或 {split_col}")
            return

        # 清理状态
        self.id_to_split.clear()
        self.conflicts.clear()
        self.unknown_split_ids.clear()
        self.not_found_ids.clear()
        self.orphan_cases.clear()
        self.plan.clear()

        # 统计
        strict = self.strict_match.get()

        # 生成行迭代（丢弃 NaN id）
        rows = []
        for _, r in self.df[[id_col, split_col]].iterrows():
            raw_id = r[id_col]
            raw_sp = r[split_col]
            if pd.isna(raw_id):
                continue

            id_key = str(raw_id) if strict else normalize_id(str(raw_id))
            sp = str(raw_sp).strip() if not pd.isna(raw_sp) else ""
            sp_key = sp.lower()

            if sp_key not in ALLOWED_SPLITS:
                # 非 train/test/validation 归为未知
                self.unknown_split_ids.add(id_key)
                continue

            # 冲突检测
            if id_key in self.id_to_split:
                if self.id_to_split[id_key] != sp_key:
                    # 记录冲突
                    self.conflicts.setdefault(id_key, set()).update({self.id_to_split[id_key], sp_key})
            else:
                self.id_to_split[id_key] = sp_key
            rows.append((id_key, sp_key))

        # 统计计数
        from collections import Counter
        cnt = Counter([sp for _, sp in rows if sp in ALLOWED_SPLITS])

        self.log("[INFO] Excel 加载完成：")
        self.log(f"  * train/test/validation 计数：{dict(cnt)}")
        if self.unknown_split_ids:
            self.log(f"  * 发现 {len(self.unknown_split_ids)} 个未知/空的划分值（将跳过）。")
        if self.conflicts:
            self.log(f"  * 发现 {len(self.conflicts)} 个 ID 存在冲突划分（将跳过）。")

        # 若已扫描 root，给出未匹配情况概览
        if self.case_dirs:
            excel_ids = set(self.id_to_split.keys())
            if strict:
                root_ids = set(self.case_dirs.keys())
            else:
                root_ids = {normalize_id(k) for k in self.case_dirs.keys()}
            self.not_found_ids = excel_ids - root_ids
            self.orphan_cases = root_ids - excel_ids if self.only_ids_in_excel.get() else set()
            self.log(f"  * Excel 中 {len(excel_ids)} 个可用 ID；与 root 对比：未在 root 找到的 ID = {len(self.not_found_ids)}；root 孤儿 = {len(self.orphan_cases)}")

    def _match_id_to_casepath(self, id_key: str):
        """根据当前匹配模式把 excel 中的 id_key 映射到实际 case 目录 Path。找不到返回 None。"""
        if self.strict_match.get():
            p = self.case_dirs.get(id_key)
            return p
        # 宽松模式：对比 normalize 后匹配
        norm_to_real = getattr(self, "_norm_to_real_cache", None)
        if norm_to_real is None:
            norm_to_real = {normalize_id(k): k for k in self.case_dirs.keys()}
            self._norm_to_real_cache = norm_to_real
        real = norm_to_real.get(id_key)
        if real is None:
            return None
        return self.case_dirs.get(real)

    def preview_plan(self):
        # 前置校验
        if not self.case_dirs:
            self.scan_root()
            if not self.case_dirs:
                return

        if self.df is None or not self.id_to_split:
            self.load_excel()
            if self.df is None or not self.id_to_split:
                return

        # 生成 plan
        self.plan = {k: [] for k in ALLOWED_SPLITS}
        skipped_conflicts = 0
        skipped_unknown = 0
        skipped_not_found = 0

        use_excel_only = self.only_ids_in_excel.get()

        # 构建 Excel ID 集
        excel_ids = set(self.id_to_split.keys())

        # 遍历 Excel 中映射
        for id_key, sp_key in self.id_to_split.items():
            if id_key in self.conflicts:
                skipped_conflicts += 1
                continue
            if id_key in self.unknown_split_ids:
                skipped_unknown += 1
                continue
            # 找 case 路径
            p = self._match_id_to_casepath(id_key)
            if p is None:
                skipped_not_found += 1
                continue
            self.plan[sp_key].append((id_key, p))

        # 若不只处理表中 ID，则把 root 中未出现在 excel 的也可忽略或提示。
        if not use_excel_only:
            self.log("[WARN] 你选择了“不过滤 Excel ID”，当前逻辑不会为孤儿数据自动分配 split，仍将跳过。")

        # 概览
        planned_total = sum(len(v) for v in self.plan.values())
        self.log("[PREVIEW] 计划梳理：")
        for sp in ("train", "test", "validation"):
            self.log(f"  - {sp}: {len(self.plan.get(sp, []))} 个 case")
        self.log(f"  * 冲突ID跳过：{skipped_conflicts}；未知/空split跳过：{skipped_unknown}；Excel有但root缺失跳过：{skipped_not_found}")
        if self.only_ids_in_excel.get():
            self.log(f"  * root 中未在 Excel 出现的孤儿 case：{len(self.orphan_cases)}（将不处理）")
        self.log(f"  * 合计将处理：{planned_total} 个 case。")

        if planned_total == 0:
            messagebox.showwarning("预览结果", "没有可执行的梳理任务，请检查 Excel 与 root。")

    def execute_plan(self):
        if not self.plan:
            self.preview_plan()
            if not self.plan:
                return

        dst = Path(self.dst_dir.get().strip())
        if not dst.exists():
            try:
                dst.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("错误", f"创建 dst 失败：{e}")
                return

        # 创建 train/test/validation 目录（若不存在）
        targets = {sp: dst / sp for sp in ALLOWED_SPLITS}
        try:
            for p in targets.values():
                p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", f"创建目标子目录失败：{e}")
            return

        # 收集任务列表
        tasks = []
        for sp, items in self.plan.items():
            for case_id, case_path in items:
                tasks.append((sp, case_id, case_path, targets[sp] / case_id))

        if not tasks:
            messagebox.showwarning("提示", "没有任务可执行。")
            return

        self.progress.configure(maximum=len(tasks), value=0)
        self.log(f"[RUN] 开始执行，共 {len(tasks)} 个目录，模式：{'移动' if self.copy_mode.get()=='move' else '复制'}")

        t = threading.Thread(target=self._do_exec, args=(tasks,), daemon=True)
        t.start()

    def _do_exec(self, tasks):
        done = 0
        failed = 0
        action = self.copy_mode.get()

        for sp, case_id, src_path, dst_path in tasks:
            try:
                if action == "move":
                    # 若目标已存在，跳过/或合并；这里采取跳过策略
                    if dst_path.exists():
                        self.log(f"[SKIP] 目标已存在（移动）：{dst_path}")
                    else:
                        shutil.move(str(src_path), str(dst_path))
                        self.log(f"[MOVE] {src_path} -> {dst_path}")
                else:
                    # 复制：允许目标已存在则合并覆盖同名文件
                    if dst_path.exists():
                        # 合并复制：逐文件复制
                        for root, dirs, files in os.walk(src_path):
                            rel = Path(root).relative_to(src_path)
                            target_dir = dst_path / rel
                            target_dir.mkdir(parents=True, exist_ok=True)
                            for f in files:
                                s = Path(root) / f
                                d = target_dir / f
                                shutil.copy2(s, d)
                        self.log(f"[COPY-MERGE] {src_path} -> {dst_path}（已存在，合并复制）")
                    else:
                        shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
                        self.log(f"[COPY] {src_path} -> {dst_path}")
            except Exception as e:
                failed += 1
                self.log(f"[ERR] 处理失败：{src_path} -> {dst_path}；原因：{e}")

            done += 1
            self.progress.configure(value=done)
            self.update_idletasks()

        self.log(f"[DONE] 完成：{done}，失败：{failed}。输出目录：{self.dst_dir.get().strip()}")
        messagebox.showinfo("完成", f"梳理完成：成功 {done - failed}，失败 {failed}。")



if __name__ == "__main__":
    app = SplitGUI()
    app.mainloop()
