"""NAIMangaMaker desktop remote. Only its owned Windows job closes with the UI."""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
import queue
import updater
from app_version import VERSION
import tkinter as tk
from tkinter import messagebox
import webbrowser

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'NAIMangaMaker'
CONFIG = DATA / 'preferences.json'
PORT = 8795
ASSETS = Path(getattr(sys, '_MEIPASS', str(ROOT))) / 'assets'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
K = c.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
K.OpenProcess.restype = w.HANDLE
K.CloseHandle.argtypes = [w.HANDLE]
K.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
K.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
K.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
K.CreateJobObjectW.restype = w.HANDLE
K.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
K.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
K.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
K.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)]

class BasicLimits(c.Structure):
    _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD),
                ('min_work', c.c_size_t), ('max_work', c.c_size_t), ('active', w.DWORD),
                ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

class IoCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]

class ExtendedLimits(c.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IoCounters), ('process_memory', c.c_size_t),
                ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]

def listening_pids(port):
    result = subprocess.run(['netstat.exe', '-ano', '-p', 'tcp'], capture_output=True,
                            text=True, creationflags=HIDDEN, timeout=5)
    if result.returncode:
        raise RuntimeError('포트 조회에 실패했습니다.')
    found = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0] == 'TCP' and fields[3] == 'LISTENING':
            if fields[1].rsplit(':', 1)[-1] == str(port):
                found.add(int(fields[4]))
    return found

class Owner:
    """Keep a handle across confirmation so recycled PIDs cannot be terminated."""
    def __init__(self, pid):
        self.pid = pid
        self.handle = K.OpenProcess(0x1000 | 1, False, pid)
        if not self.handle:
            raise OSError(f'PID {pid}에 접근할 수 없습니다. 관리자 권한이 필요할 수 있습니다.')
        buf, size = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not K.QueryFullProcessImageNameW(self.handle, 0, buf, c.byref(size)):
            self.close()
            raise OSError('점유 프로그램의 경로를 확인할 수 없습니다.')
        self.path = buf.value

    def terminate(self):
        if self.pid in (0, 4, os.getpid()):
            raise ValueError('시스템 또는 리모콘 자체는 종료할 수 없습니다.')
        if not K.TerminateProcess(self.handle, 1):
            raise c.WinError(c.get_last_error())

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None

class Server:
    def __init__(self):
        self.process = None
        self.job = None
        self.log = None

    def owns_pid(self, pid):
        if not self.job:return False
        handle=K.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            inside=w.BOOL()
            return bool(K.IsProcessInJob(handle,self.job,c.byref(inside)) and inside.value)
        finally:K.CloseHandle(handle)

    def start(self, settings):
        if self.process and self.process.poll() is None:
            raise RuntimeError('이미 실행 중입니다.')
        self.stop()
        if listening_pids(settings['port']):
            raise RuntimeError('포트가 사용 중입니다. 점유 프로그램 확인 버튼을 이용하세요.')
        self.job = K.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, including descendants.
        if not self.job or not K.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)):
            self.stop()
            raise OSError('서버 자동 종료 장치를 준비하지 못했습니다.')
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            self.log = (DATA / 'server.log').open('ab', buffering=0)
            command = [sys.executable, '--server'] if getattr(sys, 'frozen', False) else [str(Path(sys.executable).with_name('python.exe')), '-B', str(ROOT / 'remote.py'), '--server']
            command += ['--port', str(settings['port'])]
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=self.log,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=HIDDEN)
            if not K.AssignProcessToJobObject(self.job, w.HANDLE(int(self.process._handle))):
                raise OSError('서버를 자동 종료 대상으로 등록하지 못했습니다.')
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.job:
            K.TerminateJobObject(self.job, 0)
            K.CloseHandle(self.job)
            self.job = None
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            self.process = None
        if self.log:
            self.log.close()
            self.log = None

class Remote(tk.Tk):
    BG = '#f3f5f2'
    CARD = '#ffffff'
    TEXT = '#1c2931'
    MUTED = '#627078'
    ACCENT = '#24694f'

    def __init__(self):
        # Give the packaged launcher its own taskbar identity before creating a window.
        shell = c.WinDLL('shell32')
        shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [w.LPCWSTR]
        shell.SetCurrentProcessExplicitAppUserModelID.restype = c.c_long
        shell.SetCurrentProcessExplicitAppUserModelID('NAIMangaMaker.Desktop')
        super().__init__()
        self.title('NAIMangaMaker')
        self.iconbitmap(default=str(ASSETS / 'app.ico'))
        self.brand_image = tk.PhotoImage(file=str(ASSETS / 'app.png')).subsample(6,6)
        self.geometry('460x550')
        self.resizable(False, False)
        self.configure(bg=self.BG)
        self.server = Server()
        self.ready = False
        self.busy = False
        self.started = 0
        self.update_queue = queue.Queue()
        self.updating = False
        self.available_update = None
        try:
            auto = json.loads(CONFIG.read_text(encoding='utf-8')).get('auto_open', True)
        except (OSError, ValueError):
            auto = True
        self.auto = tk.BooleanVar(value=bool(auto))
        wrap = tk.Frame(self, bg=self.BG)
        wrap.pack(fill='both', expand=True, padx=32, pady=30)
        top = tk.Frame(wrap, bg=self.BG)
        top.pack(fill='x')
        tk.Label(top, image=self.brand_image, bg=self.BG, bd=0).pack(side='left')
        tk.Label(top, text='NAI MANGA MAKER', bg=self.BG, fg=self.TEXT, font=('Segoe UI',12,'bold')).pack(side='left',padx=14)
        tk.Label(wrap, text='이야기를 그릴 시간.', bg=self.BG, fg=self.TEXT, font=('맑은 고딕',24,'bold')).pack(anchor='w',pady=(32,8))
        tk.Label(wrap, text='작업실을 켜고, 다음 장면을 시작하세요.', bg=self.BG, fg=self.MUTED, font=('맑은 고딕',10)).pack(anchor='w')
        card = tk.Frame(wrap, bg=self.CARD, padx=20, pady=18)
        card.pack(fill='x',pady=(28,22))
        tk.Label(card, text='WORKSPACE', bg=self.CARD, fg=self.MUTED, font=('Segoe UI',9,'bold')).pack(anchor='w')
        self.state_label = tk.Label(card, text='●  꺼져 있음', bg=self.CARD, fg=self.MUTED,font=('맑은 고딕',13,'bold'))
        self.state_label.pack(anchor='w',pady=(9,5))
        self.detail = tk.Label(card,text='서버를 켜면 작업실을 열 수 있습니다.',bg=self.CARD,fg=self.MUTED,font=('맑은 고딕',9),wraplength=310,justify='left')
        self.detail.pack(anchor='w')
        self.toggle_button = tk.Button(wrap,text='▶   서버 켜기',command=self.toggle,bg=self.ACCENT,fg='#ffffff',
            activebackground='#185a41',activeforeground='#ffffff',font=('맑은 고딕',12,'bold'),relief='flat',bd=0,cursor='hand2',pady=12)
        self.toggle_button.pack(fill='x')
        self.web_button = tk.Button(wrap,text='작업실 열기  ↗',command=self.open_page,bg=self.BG,fg=self.MUTED,
            disabledforeground='#9ba6a0',activebackground=self.CARD,activeforeground=self.TEXT,font=('맑은 고딕',11),relief='flat',bd=0,pady=11,cursor='hand2',state='disabled')
        self.web_button.pack(fill='x',pady=(6,8))
        tk.Checkbutton(wrap,text='시작할 때 작업실 자동으로 열기',variable=self.auto,command=self.save,
            bg=self.BG,fg=self.MUTED,selectcolor=self.CARD,activebackground=self.BG,activeforeground=self.TEXT,
            font=('맑은 고딕',10),bd=0,highlightthickness=0,cursor='hand2').pack(anchor='w')
        tk.Label(wrap,text='이 창을 닫으면 서버도 함께 종료됩니다.',bg=self.BG,fg='#627078',font=('맑은 고딕',9)).pack(anchor='w',pady=(20,0))
        update_row=tk.Frame(wrap,bg=self.BG)
        update_row.pack(fill='x',pady=(16,0))
        self.update_label=tk.Label(update_row,text='v'+VERSION,bg=self.BG,fg=self.MUTED,font=('맑은 고딕',9))
        self.update_label.pack(side='left')
        self.update_button=tk.Button(update_row,text='업데이트 확인',command=self.check_updates,bg=self.CARD,fg=self.TEXT,
            font=('맑은 고딕',9),relief='flat',padx=10,pady=5,cursor='hand2')
        self.update_button.pack(side='right')
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.update_idletasks()
        self.geometry(f"460x{max(550, wrap.winfo_reqheight() + 60)}")
        self.style_titlebar()
        self.bind('<Map>', lambda event: self.style_titlebar() if event.widget is self else None)
        self.timer = self.after(400,self.tick)

    def style_titlebar(self):
        # Native caption preserves window movement, taskbar and accessibility.
        user = c.WinDLL('user32', use_last_error=True)
        user.GetParent.argtypes = [w.HWND]
        user.GetParent.restype = w.HWND
        hwnd = user.GetParent(self.winfo_id())
        user.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, c.c_int, c.c_int, w.UINT]
        user.LoadImageW.restype = w.HANDLE
        user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.SendMessageW.restype = w.LPARAM
        if not hasattr(self, '_window_icons'):
            self._window_icons = [user.LoadImageW(None, str(ASSETS / 'app.ico'), 1, size, size, 0x10) for size in (32, 64)]
        for size, icon in enumerate(self._window_icons):
            if icon: user.SendMessageW(hwnd, 0x80, size, icon)
        dwm = c.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
        for attribute, value in ((20, 0), (35, 0xf2f5f3), (36, 0x31291c), (34, 0xe2e4df)):
            color = w.DWORD(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, c.byref(color), c.sizeof(color))
        # Older Windows versions may ignore custom colors; keep native controls.

    def save(self):
        try:
            DATA.mkdir(parents=True,exist_ok=True)
            CONFIG.write_text(json.dumps({'auto_open':self.auto.get()}),encoding='utf-8')
        except OSError:
            self.detail.config(text='자동 열기 설정을 저장하지 못했습니다.')

    def confirm_port_release(self):
        owners=[]
        try:
            pids=listening_pids(PORT)
            if not pids:return True
            for pid in pids:owners.append(Owner(pid))
            details='\n'.join(f'PID {owner.pid} · {Path(owner.path).name}\n{owner.path}' for owner in owners)
            if not messagebox.askyesno('사용 중인 포트',f'작업실 포트를 다른 프로그램이 사용하고 있습니다.\n\n{details}\n\n이 프로그램을 종료하고 새 작업실을 켤까요?\n진행 중인 작업은 중단됩니다.',parent=self):return False
            for owner in owners:owner.terminate()
            until=time.monotonic()+3
            while listening_pids(PORT):
                if time.monotonic()>until:raise OSError('포트가 아직 사용 중입니다. 잠시 후 다시 시도해 주세요.')
                time.sleep(.1)
            return True
        finally:
            for owner in owners:owner.close()

    def toggle(self):
        if self.busy:return
        if self.server.process:
            self.stop()
            return
        self.busy=True
        self.toggle_button.config(state='disabled')
        try:
            if not self.confirm_port_release():return
            self.server.start({'port':PORT})
            self.started=time.monotonic()
            self.ready=False
            self.state_label.config(text='●  준비 중',fg=self.ACCENT)
            self.detail.config(text='작업실을 준비하고 있습니다…')
            self.toggle_button.config(text='■   서버 끄기',bg='#e8f3ed',fg='#24694f')
        except Exception as exc:
            self.detail.config(text='시작하지 못했습니다. 다시 시도해 주세요.')
            messagebox.showerror('서버 시작',str(exc),parent=self)
        finally:
            self.busy=False
            self.toggle_button.config(state='normal')

    def tick(self):
        if self.drain_updates():return
        proc=self.server.process
        if proc:
            if proc.poll() is not None:
                code=proc.returncode
                self.stop()
                self.detail.config(text=f'서버가 종료됐습니다 (코드 {code}).')
            elif not self.ready:
                try:
                    with socket.create_connection(('127.0.0.1',PORT),timeout=.15):pass
                    if not any(self.server.owns_pid(pid) for pid in listening_pids(PORT)):raise OSError('다른 프로그램이 포트를 사용합니다.')
                    self.ready=True
                    self.state_label.config(text='●  작업실 실행 중',fg='#24694f')
                    self.detail.config(text='준비됐습니다. 브라우저에서 작업을 시작하세요.')
                    self.web_button.config(state='normal',fg=self.TEXT)
                    if self.auto.get():self.open_page()
                except (OSError,subprocess.SubprocessError):
                    if time.monotonic()-self.started>30:self.detail.config(text='응답을 기다리고 있습니다. 끈 뒤 다시 켤 수 있습니다.')
        self.timer = self.after(600,self.tick)

    def stop(self):
        self.server.stop()
        self.ready=False
        self.state_label.config(text='●  꺼져 있음',fg=self.MUTED)
        self.detail.config(text='서버를 켜면 작업실을 열 수 있습니다.')
        self.toggle_button.config(text='▶   서버 켜기',bg=self.ACCENT,fg='#ffffff')
        self.web_button.config(state='disabled',fg=self.MUTED)

    def open_page(self):
        if self.ready:webbrowser.open(f'http://127.0.0.1:{PORT}/')

    def check_updates(self):
        if self.updating:return
        if self.available_update:
            if not getattr(sys,'frozen',False):
                webbrowser.open(updater.RELEASES_URL)
                return
            if not messagebox.askyesno('업데이트 설치',self.available_update['version']+' 버전을 다운로드하고 적용할까요?\n적용 시 리모콘과 서버가 종료된 뒤 새 버전이 열립니다. 진행 중인 생성은 먼저 완료해 주세요.\n개인 설정과 작품은 유지됩니다.',parent=self):return
            self.updating=True;self.update_button.config(state='disabled');self.update_label.config(text='다운로드 준비 중…')
            def download():
                try:
                    path=updater.download(self.available_update,DATA,lambda p:self.update_queue.put(('progress',p)))
                    self.update_queue.put(('downloaded',path))
                except Exception as error:self.update_queue.put(('error',str(error)))
            threading.Thread(target=download,daemon=True).start()
            return
        self.updating=True;self.update_button.config(state='disabled');self.update_label.config(text='새 버전 확인 중…')
        def check():
            try:self.update_queue.put(('checked',updater.latest()))
            except Exception as error:self.update_queue.put(('error',str(error)))
        threading.Thread(target=check,daemon=True).start()

    def drain_updates(self):
        while True:
            try:kind,value=self.update_queue.get_nowait()
            except queue.Empty:return
            if kind=='progress':self.update_label.config(text=f'다운로드 {value}%')
            elif kind=='checked':
                self.updating=False;self.available_update=value;self.update_button.config(state='normal')
                self.update_label.config(text=value['version']+' 사용 가능' if value else 'v'+VERSION+' · 최신 버전')
                self.update_button.config(text='업데이트 설치' if value else '업데이트 확인')
                if value:
                    messagebox.showinfo('새 업데이트',value['version']+' 버전이 있습니다.\n\n'+value['notes'][:1800],parent=self)
            elif kind=='downloaded':
                try:
                    updater.launch_installer(value,self.available_update['sha256'])
                    self.close()
                    return True
                except Exception as error:self.update_queue.put(('error',str(error)))
            elif kind=='error':
                self.updating=False;self.update_button.config(state='normal')
                self.update_label.config(text='업데이트 확인 필요')
                messagebox.showerror('업데이트',value+'\n기존 프로그램과 개인 데이터는 유지됩니다.',parent=self)

    def close(self):
        self.after_cancel(self.timer)
        self.save()
        self.stop()
        self.destroy()

if __name__ == '__main__':
    if '--apply-update' in sys.argv:
        try:updater.apply_update(sys.argv[sys.argv.index('--apply-update')+1])
        except Exception as error:
            c.windll.user32.MessageBoxW(None,str(error)+'\n기존 EXE 또는 .previous.exe로 다시 실행할 수 있습니다.','NAIMangaMaker 업데이트',0x10)
    elif '--diagnostics' in sys.argv:
        output=Path(sys.argv[sys.argv.index('--diagnostics')+1])
        try:
            from tag_catalog import catalog
            from engine.image_preferences import original_defaults
            root=Remote();root.withdraw();root.update_idletasks()
            output.write_text(json.dumps(dict(version=VERSION,tags=len(catalog()),defaults=original_defaults(),
                ui_height=root.winfo_reqheight(),icon_present=(ASSETS/'app.ico').exists()),ensure_ascii=False),encoding='utf-8')
            root.after_cancel(root.timer);root.destroy()
        except Exception:
            import traceback
            output.write_text(json.dumps(dict(version=VERSION,error=traceback.format_exc()),ensure_ascii=False),encoding='utf-8')
            raise SystemExit(1)
    elif '--server' in sys.argv:
        from server import serve
        import argparse
        parser=argparse.ArgumentParser()
        parser.add_argument('--server',action='store_true')
        parser.add_argument('--port',type=int,default=PORT)
        serve(parser.parse_args().port)
    else:
        Remote().mainloop()
