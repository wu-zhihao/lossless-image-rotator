#!/usr/bin/env python3
"""
TIFF 批量旋转工具 v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
专业天文摄影 16-bit TIFF 无损旋转
完整保留 EXIF / XMP / ICC / IPTC 等全部元数据
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import struct
import numpy as np
import os
import shutil
import threading
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
#  核心旋转引擎 — 二进制级别操作，保留所有元数据
# ═══════════════════════════════════════════════════════════════

# rotation k values for np.rot90
ROT_MAP = {
    'cw90': 3,    # 顺时针 90° = 逆时针 270°
    'ccw90': 1,   # 逆时针 90°
    '180': 2,     # 180°
}

DIR_NAMES = {
    'cw90': '顺时针 90°',
    'ccw90': '逆时针 90°',
    '180': '180°',
}


def get_tiff_info(filepath):
    """快速读取 TIFF 关键信息（大小、尺寸、位深）"""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(8)
            byteorder = '<' if header[:2] == b'II' else '>'
            ifd0_offset = struct.unpack(f'{byteorder}I', header[4:8])[0]

            f.seek(ifd0_offset)
            num_entries = struct.unpack(f'{byteorder}H', f.read(2))[0]

            info = {}
            for _ in range(num_entries):
                entry = f.read(12)
                tag_id = struct.unpack(f'{byteorder}H', entry[:2])[0]
                dtype = struct.unpack(f'{byteorder}H', entry[2:4])[0]
                count = struct.unpack(f'{byteorder}I', entry[4:8])[0]
                vb = entry[8:12]

                # 根据 dtype 解析值
                if dtype == 3 and count == 1:  # SHORT, single value
                    v = struct.unpack(f'{byteorder}H', vb[:2])[0]
                elif dtype == 3 and count == 3:  # SHORT × 3 (BitsPerSample)
                    v = struct.unpack(f'{byteorder}H', vb[:2])[0]
                else:
                    v = struct.unpack(f'{byteorder}I', vb)[0]

                if tag_id == 256:
                    info['width'] = v
                elif tag_id == 257:
                    info['height'] = v
                elif tag_id == 258:
                    info['bits'] = v  # first component of BitsPerSample
                elif tag_id == 259:
                    info['compression'] = v

            info['size_mb'] = os.path.getsize(filepath) / (1024 * 1024)
            return info
    except Exception:
        return None


def rotate_tiff(src_path, dst_path, direction='cw90', log_func=None):
    """
    旋转单个 TIFF 文件，完整保留所有元数据。

    流程：
    1. 复制原文件 → 输出目录
    2. 读取原始像素数据 (16-bit uint16)
    3. numpy.rot90 旋转
    4. 写回像素数据（原地替换，不改变文件结构）
    5. 更新 IFD0 中 ImageWidth / ImageLength / RowsPerStrip / StripByteCounts

    所有其他标签（EXIF、XMP、ICC、IPTC、Photoshop Resources）
    保持原封不动。
    """
    rot_k = ROT_MAP[direction]

    # 1. 复制原文件
    shutil.copy2(src_path, dst_path)

    with open(dst_path, 'r+b') as f:
        # 2. 读 TIFF 头
        header = f.read(8)
        byteorder = '<' if header[:2] == b'II' else '>'
        ifd0_offset = struct.unpack(f'{byteorder}I', header[4:8])[0]

        # 3. 读 IFD0 所有条目
        f.seek(ifd0_offset)
        num_entries = struct.unpack(f'{byteorder}H', f.read(2))[0]

        tags = {}  # tag_id → {pos, dtype, count, value_bytes}
        for _ in range(num_entries):
            pos = f.tell()
            entry = f.read(12)
            tag_id = struct.unpack(f'{byteorder}H', entry[:2])[0]
            dtype = struct.unpack(f'{byteorder}H', entry[2:4])[0]
            count = struct.unpack(f'{byteorder}I', entry[4:8])[0]
            value_bytes = entry[8:12]
            tags[tag_id] = {
                'pos': pos, 'dtype': dtype,
                'count': count, 'value_bytes': value_bytes,
            }

        def tag_long(ti):
            return struct.unpack(f'{byteorder}I', ti['value_bytes'])[0]

        old_w = tag_long(tags[256])   # ImageWidth
        old_h = tag_long(tags[257])   # ImageLength
        strip_off = tag_long(tags[273])  # StripOffsets
        strip_len = tag_long(tags[279])  # StripByteCounts

        # 4. 读取 + 旋转像素
        f.seek(strip_off)
        raw = f.read(strip_len)
        arr = np.frombuffer(raw, dtype=np.uint16).reshape(old_h, old_w, 3)
        rotated = np.rot90(arr, k=rot_k)
        new_h, new_w = rotated.shape[0], rotated.shape[1]

        # 5. 写回像素
        f.seek(strip_off)
        new_bytes = rotated.tobytes()
        f.write(new_bytes)
        new_len = len(new_bytes)
        if new_len < strip_len:
            f.truncate(strip_off + new_len)

        # 6. 更新 IFD0 标签值（只改 4 个字段的 value 部分，不动 IFD 结构）
        f.seek(tags[256]['pos'] + 8)
        f.write(struct.pack(f'{byteorder}I', new_w))
        f.seek(tags[257]['pos'] + 8)
        f.write(struct.pack(f'{byteorder}I', new_h))
        f.seek(tags[278]['pos'] + 8)
        f.write(struct.pack(f'{byteorder}I', new_h))
        f.seek(tags[279]['pos'] + 8)
        f.write(struct.pack(f'{byteorder}I', new_len))

    info = f'{old_w}×{old_h} → {new_w}×{new_h}'
    if log_func:
        log_func(f'[OK] {os.path.basename(src_path)}: {info}')
    return True, info


# ═══════════════════════════════════════════════════════════════
#  GUI 应用程序
# ═══════════════════════════════════════════════════════════════

class TiffRotatorApp:
    """TIFF 批量旋转 GUI 应用"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('TIFF 批量旋转工具 v2.0')
        self.root.geometry('860x660')
        self.root.minsize(700, 500)

        # 应用状态
        self.files: list[str] = []
        self.processing = False
        self.stop_flag = False

        # 界面引用（在 _build_ui 中赋值）
        self.tree: ttk.Treeview
        self.count_label: ttk.Label
        self.rot_var: tk.StringVar
        self.out_dir_var: tk.StringVar
        self.use_subfolder_var: tk.BooleanVar
        self.progress_var: tk.DoubleVar
        self.progress_label: ttk.Label
        self.log_text: tk.Text
        self.start_btn: ttk.Button
        self.stop_btn: ttk.Button

        self._build_ui()

    # ── UI 构建 ──────────────────────────────────────────────

    def _build_ui(self):
        main = ttk.Frame(self.root, padding='12')
        main.pack(fill='both', expand=True)

        # ──── 文件列表 ────
        list_frame = ttk.LabelFrame(main, text='待处理文件', padding='6')
        list_frame.pack(fill='both', expand=True, pady=(0, 6))

        # 工具栏
        bar = ttk.Frame(list_frame)
        bar.pack(fill='x', pady=(0, 6))

        ttk.Button(bar, text='📂 添加文件', command=self._add_files).pack(side='left', padx=2)
        ttk.Button(bar, text='📁 添加文件夹', command=self._add_folder).pack(side='left', padx=2)
        ttk.Button(bar, text='✕ 移除选中', command=self._remove_selected).pack(side='left', padx=2)
        ttk.Button(bar, text='🗑 清空列表', command=self._clear_all).pack(side='left', padx=2)
        ttk.Separator(bar, orient='vertical').pack(side='left', padx=10, fill='y')
        ttk.Label(bar, text='💡 可拖放文件到列表区域').pack(side='left', padx=6)
        self.count_label = ttk.Label(bar, text='0 个文件')
        self.count_label.pack(side='right')

        # Treeview 文件列表
        tree_frame = ttk.Frame(list_frame)
        tree_frame.pack(fill='both', expand=True)

        cols = ('name', 'size', 'dimensions', 'path')
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings',
                                 selectmode='extended', height=8)
        self.tree.heading('name', text='文件名')
        self.tree.heading('size', text='大小 (MB)')
        self.tree.heading('dimensions', text='原始尺寸')
        self.tree.heading('path', text='所在目录')
        self.tree.column('name', width=180, minwidth=100)
        self.tree.column('size', width=75, minwidth=55, anchor='e')
        self.tree.column('dimensions', width=130, minwidth=90, anchor='center')
        self.tree.column('path', width=300, minwidth=120)

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # ──── 设置 ────
        settings = ttk.LabelFrame(main, text='设置', padding='10')
        settings.pack(fill='x', pady=4)

        # 旋转方向
        ttk.Label(settings, text='旋转方向:').grid(row=0, column=0, sticky='w')
        self.rot_var = tk.StringVar(value='cw90')
        rf = ttk.Frame(settings)
        rf.grid(row=0, column=1, columnspan=2, sticky='w', padx=(10, 0))
        ttk.Radiobutton(rf, text='顺时针 90°', variable=self.rot_var, value='cw90').pack(side='left', padx=6)
        ttk.Radiobutton(rf, text='逆时针 90°', variable=self.rot_var, value='ccw90').pack(side='left', padx=6)
        ttk.Radiobutton(rf, text='180°', variable=self.rot_var, value='180').pack(side='left', padx=6)

        # 输出目录
        ttk.Label(settings, text='输出目录:').grid(row=1, column=0, sticky='w', pady=(10, 0))
        of = ttk.Frame(settings)
        of.grid(row=1, column=1, sticky='ew', pady=(10, 0), padx=(10, 0))
        self.out_dir_var = tk.StringVar()
        ttk.Entry(of, textvariable=self.out_dir_var, state='readonly').pack(side='left', fill='x', expand=True, padx=(0, 5))
        ttk.Button(of, text='浏览...', command=self._browse_output).pack(side='left')

        self.use_subfolder_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings, text='在源文件夹下创建"旋转后"子文件夹（未指定输出目录时）',
                        variable=self.use_subfolder_var).grid(
            row=2, column=1, sticky='w', pady=(4, 0), padx=(10, 0))

        settings.grid_columnconfigure(1, weight=1)

        # ──── 进度条 ────
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(main, variable=self.progress_var, mode='determinate').pack(fill='x', pady=(10, 2))
        self.progress_label = ttk.Label(main, text='就绪 — 请添加文件后开始处理')
        self.progress_label.pack(anchor='w')

        # ──── 日志 ────
        log_frame = ttk.LabelFrame(main, text='处理日志', padding='5')
        log_frame.pack(fill='both', expand=True, pady=(6, 8))

        self.log_text = tk.Text(log_frame, height=6, font=('Consolas', 9), wrap='none',
                                state='disabled', bg='#1e1e1e', fg='#d4d4d4',
                                insertbackground='white', relief='flat')
        self.log_text.pack(fill='both', expand=True)

        # ──── 底部按钮 ────
        bf = ttk.Frame(main)
        bf.pack(fill='x')
        self.start_btn = ttk.Button(bf, text='▶  开始处理', command=self._start_processing, width=14)
        self.start_btn.pack(side='right', padx=3)
        self.stop_btn = ttk.Button(bf, text='⏹ 停止', command=self._stop_processing,
                                    state='disabled', width=8)
        self.stop_btn.pack(side='right', padx=3)

    # ── 文件管理 ────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title='选择 TIFF 文件',
            filetypes=[('TIFF 图像', '*.tif *.tiff'), ('所有文件', '*.*')])
        if paths:
            self._add_file_list(paths)

    def _add_folder(self):
        folder = filedialog.askdirectory(title='选择包含 TIFF 文件的文件夹')
        if folder:
            collected = []
            for dirpath, _, filenames in os.walk(folder):
                for fn in filenames:
                    if fn.lower().endswith(('.tif', '.tiff')):
                        collected.append(os.path.join(dirpath, fn))
            self._add_file_list(collected)

    def _add_file_list(self, paths):
        added = 0
        for p in paths:
            p = str(p)
            if p in self.files:
                continue
            if not p.lower().endswith(('.tif', '.tiff')):
                continue
            if not os.path.isfile(p):
                continue

            info = get_tiff_info(p)
            if info is None:
                self._log(f'[跳过] 无法解析: {os.path.basename(p)}')
                continue

            self.files.append(p)
            self.tree.insert('', 'end', values=(
                os.path.basename(p),
                f'{info["size_mb"]:.1f}',
                f'{info["width"]} × {info["height"]}',
                os.path.dirname(p),
            ))
            added += 1

        if added:
            self._update_count()
            self._log(f'✓ 已添加 {added} 个文件')

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        indices = sorted([self.tree.index(s) for s in sel], reverse=True)
        for i in indices:
            self.tree.delete(self.tree.get_children()[i])
            del self.files[i]
        self._update_count()

    def _clear_all(self):
        if self.processing:
            messagebox.showwarning('提示', '处理进行中，请先停止。')
            return
        self.tree.delete(*self.tree.get_children())
        self.files.clear()
        self._update_count()

    def _update_count(self):
        n = len(self.files)
        self.count_label.configure(text=f'{n} 个文件')

    # ── 输出目录 ────────────────────────────────────────────

    def _browse_output(self):
        folder = filedialog.askdirectory(title='选择输出目录')
        if folder:
            self.out_dir_var.set(folder)
            self.use_subfolder_var.set(False)

    # ── 日志 ────────────────────────────────────────────────

    def _log(self, msg: str):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    # ── 处理流程 ────────────────────────────────────────────

    def _start_processing(self):
        if self.processing:
            return
        if not self.files:
            messagebox.showwarning('提示', '请先添加 TIFF 文件。')
            return

        self.processing = True
        self.stop_flag = False
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.progress_var.set(0)
        self.progress_label.configure(text='准备中...')
        self._log('══════════════════════════════════════')

        direction = self.rot_var.get()
        self._log(f'开始处理 {len(self.files)} 个文件 — {DIR_NAMES[direction]}')

        thread = threading.Thread(target=self._process_worker,
                                  args=(self.use_subfolder_var.get(),
                                        self.out_dir_var.get().strip()),
                                  daemon=True)
        thread.start()

    def _stop_processing(self):
        self.stop_flag = True
        self.stop_btn.configure(state='disabled')
        self._log('⏹ 用户请求停止...')

    def _process_worker(self, use_subfolder: bool, custom_out: str):
        total = len(self.files)
        direction = self.rot_var.get()
        ok = 0
        fail = 0

        for idx, src_path in enumerate(self.files):
            if self.stop_flag:
                msg = f'已停止 — 完成 {ok}，失败 {fail}，剩余 {total - idx}'
                self.root.after(0, lambda m=msg: self._log(m))
                break

            filename = os.path.basename(src_path)
            src_dir = os.path.dirname(src_path)

            # 确定输出目录
            if custom_out:
                out_dir = custom_out
            elif use_subfolder:
                out_dir = os.path.join(src_dir, '旋转后')
            else:
                out_dir = src_dir
            os.makedirs(out_dir, exist_ok=True)

            dst_path = os.path.join(out_dir, filename)

            # 更新进度
            pct = (idx / total) * 100
            self.root.after(0, lambda p=pct, i=idx, t=total:
                            self._set_progress(p, f'处理中... {i}/{t}'))

            # 执行旋转
            try:
                success, _info = rotate_tiff(src_path, dst_path, direction, log_func=None)
                if success:
                    ok += 1
                    # 用默认参数捕获当前值避免闭包延迟绑定
                    self.root.after(0, lambda fn=filename, inf=_info: self._log(f'[OK] {fn}: {inf}'))
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                self.root.after(0, lambda fn=filename, err=str(e):
                                self._log(f'[FAIL] {fn}: {err}'))

        # 完成
        summary = f'—— 处理完毕：成功 {ok}/{total}，失败 {fail} ——'
        self.root.after(0, lambda: self._set_progress(100, f'完成！成功 {ok}，失败 {fail}'))
        self.root.after(0, lambda: self._log(summary))
        self.root.after(0, self._processing_done)

        if not self.stop_flag and ok > 0:
            self.root.after(800, lambda: messagebox.showinfo(
                '处理完成',
                f'全部处理完毕！\n\n'
                f'成功: {ok} 个\n'
                f'失败: {fail} 个\n'
                f'总计: {total} 个'))

    def _set_progress(self, pct: float, text: str):
        self.progress_var.set(pct)
        self.progress_label.configure(text=text)

    def _processing_done(self):
        self.processing = False
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    TiffRotatorApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
