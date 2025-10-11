import os
import re
import threading
import traceback
from collections import defaultdict, Counter
from tkinter import *
from tkinter import ttk, filedialog, messagebox

# 依赖
try:
    import pydicom
except Exception:
    pydicom = None

try:
    import SimpleITK as sitk
except Exception:
    sitk = None


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def safe_join(*parts):
    return os.path.normpath(os.path.join(*parts))


def find_ids(root):
    if not os.path.isdir(root):
        return []
    return [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]


def scan_dicom_structure(root):
    """
    扫描 root/ID/scan/series 下的 .dcm/.dicom
    输出 records（每条为一个可转换单元），stats（统计）
    """
    exts = {'.dcm', '.dicom'}
    records = []
    total_files = 0
    series_folders = 0
    ids = find_ids(root)

    for pid in ids:
        id_dir = safe_join(root, pid)
        for scan in os.listdir(id_dir):
            scan_dir = safe_join(id_dir, scan)
            if not os.path.isdir(scan_dir):
                continue
            for series in os.listdir(scan_dir):
                series_dir = safe_join(scan_dir, series)
                if not os.path.isdir(series_dir):
                    continue

                files = []
                for fn in os.listdir(series_dir):
                    p = safe_join(series_dir, fn)
                    if os.path.isfile(p):
                        _, ext = os.path.splitext(fn)
                        if ext.lower() in exts:
                            files.append(p)
                if not files:
                    continue

                series_folders += 1
                total_files += len(files)

                # 以 ^(\d+)- 前缀拆分组；无此前缀的归到 'all'
                groups = defaultdict(list)
                for f in files:
                    base = os.path.basename(f)
                    m = re.match(r'^(\d+)-', base)
                    if m:
                        groups[m.group(1)].append(f)
                    else:
                        groups['all'].append(f)

                for seq_label, flist in groups.items():
                    flist.sort(key=natural_key)
                    example_meta = {}
                    if pydicom and flist:
                        try:
                            ds = pydicom.dcmread(flist[0], stop_before_pixels=True, force=True)
                            for k in ["Modality", "SeriesDescription", "Series Description",
                                      "SeriesInstanceUID", "StudyDescription", "PatientID"]:
                                k1 = k.replace(" ", "")
                                if hasattr(ds, k1):
                                    example_meta[k] = str(getattr(ds, k1))
                                elif k in ds:
                                    example_meta[k] = str(ds.get(k, ""))
                                elif k1 in ds:
                                    example_meta[k] = str(ds.get(k1, ""))
                        except Exception:
                            example_meta = {"_meta_error": "failed to read metadata"}

                    records.append({
                        "id": pid,
                        "scan": scan,
                        "series": series,
                        "seq_label": seq_label,
                        "files": flist,
                        "example_meta": example_meta
                    })

    stats = {
        "num_ids": len(ids),
        "num_series_folders": series_folders,
        "num_dicom_files": total_files,
        "num_groups": len(records),
    }
    return records, stats


def sort_by_instance_number(files):
    if not pydicom:
        return sorted(files, key=natural_key)

    items = []
    for f in files:
        inst = None
        try:
            ds = pydicom.dcmread(f, stop_before_pixels=True, force=True)
            inst = int(getattr(ds, "InstanceNumber", None) or ds.get("InstanceNumber") or 0)
        except Exception:
            inst = None
        items.append((f, inst))

    with_num = [(f, inst) for (f, inst) in items if isinstance(inst, int)]
    without_num = [f for (f, inst) in items if not isinstance(inst, int)]

    with_num.sort(key=lambda x: x[1])
    without_num.sort(key=natural_key)

    return [f for (f, _) in with_num] + without_num


def read_series_to_image(file_list):
    if not sitk:
        raise RuntimeError("SimpleITK 未安装，无法进行图像转换。")
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(file_list)
    img = reader.Execute()
    return img


def ensure_unique_path(path):
    """若存在同名，则添加 _2, _3 …；兼容 .nii.gz 双扩展"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    if base.endswith(".nii"):
        root = base[:-4]
        ext = ".nii.gz"
        idx = 2
        candidate = f"{root}_{idx}{ext}"
        while os.path.exists(candidate):
            idx += 1
            candidate = f"{root}_{idx}{ext}"
        return candidate
    idx = 2
    candidate = f"{base}_{idx}{ext}"
    while os.path.exists(candidate):
        idx += 1
        candidate = f"{base}_{idx}{ext}"
    return candidate


def make_output_name_default(scan, series):
    name = f"{scan}_{series}.nii.gz"
    return re.sub(r'[\\/:*?"<>|]+', "_", name)


def make_output_name_custom(first_file, meta_keys):
    if not pydicom:
        raise RuntimeError("pydicom 未安装，无法使用自定义命名。")
    ds = pydicom.dcmread(first_file, stop_before_pixels=True, force=True)
    values = []
    for key in meta_keys:
        k1 = key.replace(" ", "")
        val = None
        if hasattr(ds, k1):
            val = getattr(ds, k1)
        elif key in ds:
            val = ds.get(key)
        elif k1 in ds:
            val = ds.get(k1)
        if val is None:
            val = "NULL"
        sval = re.sub(r'\s+', ' ', str(val)).strip()
        sval = re.sub(r'[\\/:*?"<>|]+', "_", sval)
        values.append(sval if sval else "NULL")
    joined = "_".join(values) if values else "unnamed"
    return f"{joined}.nii.gz"


class Dcm2NiiGUI:
    def __init__(self, master):
        self.master = master
        master.title("课题组DICOM → NIfTI转换小助手")

        self.root_dir = StringVar()
        self.dst_dir = StringVar()

        self.naming_mode = StringVar(value="default")
        self.meta_keys = []

        self.records = []
        self.stats = {}

        self._stop_flag = threading.Event()

        self.build_ui()

    def build_ui(self):
        frm_top = ttk.Frame(self.master, padding=8)
        frm_top.pack(fill=X)

        ttk.Label(frm_top, text="数据根目录 (root):").grid(row=0, column=0, sticky=W)
        ttk.Entry(frm_top, textvariable=self.root_dir, width=60).grid(row=0, column=1, sticky=EW, padx=5)
        ttk.Button(frm_top, text="浏览...", command=self.choose_root).grid(row=0, column=2, sticky=E)

        ttk.Label(frm_top, text="输出目录 (dst):").grid(row=1, column=0, sticky=W)
        ttk.Entry(frm_top, textvariable=self.dst_dir, width=60).grid(row=1, column=1, sticky=EW, padx=5)
        ttk.Button(frm_top, text="浏览...", command=self.choose_dst).grid(row=1, column=2, sticky=E)

        frm_top.grid_columnconfigure(1, weight=1)

        # 预览
        frm_preview = ttk.Frame(self.master, padding=8)
        frm_preview.pack(fill=BOTH, expand=True)
        ttk.Button(frm_preview, text="扫描并预览", command=self.on_preview).pack(anchor=W)

        columns = ("id", "scan", "series", "seq", "num", "meta")
        self.tree = ttk.Treeview(frm_preview, columns=columns, show="headings", height=10)
        for col, w in zip(columns, (120, 120, 150, 60, 80, 420)):
            self.tree.heading(col, text=col.upper())
            self.tree.column(col, width=w, anchor=W)
        self.tree.pack(fill=BOTH, expand=True, pady=6)

        self.stats_var = StringVar()
        ttk.Label(frm_preview, textvariable=self.stats_var, foreground="#444").pack(anchor=W, pady=(0, 6))

        # 命名
        labframe = ttk.LabelFrame(self.master, text="输出命名规则", padding=8)
        labframe.pack(fill=X, padx=8, pady=4)
        ttk.Radiobutton(labframe, text="默认：{scan}_{series}.nii.gz",
                        variable=self.naming_mode, value="default").grid(row=0, column=0, sticky=W)
        ttk.Radiobutton(labframe, text="自定义（按 DICOM Metadata key 拼接）",
                        variable=self.naming_mode, value="custom").grid(row=1, column=0, sticky=W)

        self.meta_container = ttk.Frame(labframe)
        self.meta_container.grid(row=2, column=0, columnspan=3, sticky=EW, pady=4)
        ttk.Button(labframe, text=" + 增加Key ", command=self.add_meta_key).grid(row=1, column=1, padx=8)
        ttk.Button(labframe, text=" 清空Key ", command=self.clear_meta_keys).grid(row=1, column=2)

        # 运行区
        frm_run = ttk.Frame(self.master, padding=8)
        frm_run.pack(fill=X)
        self.pb = ttk.Progressbar(frm_run, mode="determinate")
        self.pb.pack(fill=X, pady=4)

        btns = ttk.Frame(frm_run)
        btns.pack(fill=X)
        ttk.Button(btns, text="开始转换", command=self.on_convert).pack(side=LEFT)
        ttk.Button(btns, text="停止", command=self.on_stop).pack(side=LEFT, padx=6)

        # 日志
        frm_log = ttk.LabelFrame(self.master, text="日志", padding=8)
        frm_log.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))
        self.txt_log = Text(frm_log, height=10)
        self.txt_log.pack(fill=BOTH, expand=True)

    def choose_root(self):
        d = filedialog.askdirectory(title="选择 root 目录")
        if d:
            self.root_dir.set(d)

    def choose_dst(self):
        d = filedialog.askdirectory(title="选择 dst 目录")
        if d:
            self.dst_dir.set(d)

    def add_meta_key(self):
        sv = StringVar()
        row = len(self.meta_keys)
        frm = ttk.Frame(self.meta_container)
        frm.grid(row=row, column=0, sticky=EW, pady=2)
        e = ttk.Entry(frm, textvariable=sv, width=40)
        e.pack(side=LEFT)
        ttk.Button(frm, text="删除", command=lambda f=frm, v=sv: self.remove_meta_key(f, v)).pack(side=LEFT, padx=4)
        self.meta_keys.append(sv)

    def remove_meta_key(self, frame, var):
        frame.destroy()
        self.meta_keys.remove(var)

    def clear_meta_keys(self):
        for child in list(self.meta_container.children.values()):
            child.destroy()
        self.meta_keys.clear()

    def log(self, msg):
        self.txt_log.insert(END, msg + "\n")
        self.txt_log.see(END)
        self.master.update_idletasks()

    def on_preview(self):
        root = self.root_dir.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror("错误", "请先选择有效的 root 目录。")
            return
        self.tree.delete(*self.tree.get_children())
        self.txt_log.delete("1.0", END)
        self.log("[INFO] 开始扫描 ...")
        try:
            records, stats = scan_dicom_structure(root)
            self.records = records
            self.stats = stats
            for rec in records:
                meta_summary = ""
                if rec["example_meta"]:
                    pairs = list(rec["example_meta"].items())[:3]
                    meta_summary = "; ".join([f"{k}: {v}" for k, v in pairs])
                self.tree.insert("", END, values=(
                    rec["id"], rec["scan"], rec["series"],
                    rec["seq_label"], len(rec["files"]), meta_summary
                ))
            stat_msg = (f"IDs: {stats['num_ids']} | Series folders: {stats['num_series_folders']} | "
                        f"DICOM files: {stats['num_dicom_files']} | Sequence groups: {stats['num_groups']}")
            self.stats_var.set(stat_msg)
            self.log("[INFO] 扫描完成。")
            self.log("[INFO] " + stat_msg)
            self.pb["maximum"] = len(self.records)
            self.pb["value"] = 0
        except Exception:
            self.log("[ERROR] 扫描失败：\n" + traceback.format_exc())
            messagebox.showerror("错误", "扫描失败，请查看日志。")

    def on_stop(self):
        self._stop_flag.set()
        self.log("[INFO] 已请求停止。")

    def on_convert(self):
        if not self.records:
            messagebox.showwarning("提示", "请先扫描并预览。")
            return
        if not self.dst_dir.get().strip():
            messagebox.showwarning("提示", "请先选择输出目录 dst。")
            return
        if self.naming_mode.get() == "custom" and not self.meta_keys:
            messagebox.showwarning("提示", "自定义命名模式下，请至少添加一个 DICOM Metadata key。")
            return
        if sitk is None:
            messagebox.showerror("错误", "未检测到 SimpleITK，请先安装：pip install SimpleITK")
            return
        if self.naming_mode.get() == "custom" and pydicom is None:
            messagebox.showerror("错误", "未检测到 pydicom，自定义命名需要 pydicom：pip install pydicom")
            return

        self._stop_flag.clear()
        self.txt_log.delete("1.0", END)
        t = threading.Thread(target=self._do_convert_thread, daemon=True)
        t.start()

    def _do_convert_thread(self):
        mode = self.naming_mode.get()
        meta_keys = [sv.get().strip() for sv in self.meta_keys]
        dst_root = self.dst_dir.get().strip()

        id_counter = Counter()
        self.pb["maximum"] = len(self.records)
        self.pb["value"] = 0
        self.log("[INFO] 开始转换 ...")

        for idx, rec in enumerate(self.records, 1):
            if self._stop_flag.is_set():
                self.log("[INFO] 已停止。")
                break
            try:
                pid = rec["id"]
                scan = rec["scan"]
                series = rec["series"]
                files = rec["files"]

                files_sorted = sort_by_instance_number(files)
                img = read_series_to_image(files_sorted)

                # ====== 仅创建 dst/ID 文件夹（不复制 root 中的 scan/series 结构）======
                out_dir = safe_join(dst_root, pid)   # 只有 ID 层级
                os.makedirs(out_dir, exist_ok=True)
                # ============================================================

                if mode == "default":
                    out_name = make_output_name_default(scan, series)
                else:
                    out_name = make_output_name_custom(files_sorted[0], meta_keys)

                out_path = ensure_unique_path(safe_join(out_dir, out_name))
                sitk.WriteImage(img, out_path, useCompression=True)

                id_counter[pid] += 1
                self.log(f"[OK] {pid} | {scan}/{series} ({rec['seq_label']}) -> {out_path}")
            except Exception:
                self.log("[ERROR] 转换失败：\n" + traceback.format_exc())
            finally:
                self.pb["value"] = idx
                self.master.update_idletasks()

        if not self._stop_flag.is_set():
            self.log("[INFO] 转换完成。")
            summary = " | ".join([f"{k}:{v}" for k, v in id_counter.items()])
            if summary:
                self.log("[SUMMARY] 每个ID的输出数量： " + summary)
            messagebox.showinfo("完成", "全部转换完成。")


if __name__ == "__main__":
    root = Tk()
    root.geometry("1100x700")
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    app = Dcm2NiiGUI(root)
    root.mainloop()
