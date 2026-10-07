"""Expose the bundled compiler through a transient ASCII directory junction."""
import atexit
import ctypes
import os
import subprocess
import tempfile
from pathlib import Path


def compiler_environment(root, inherited=None):
    inherited = os.environ if inherited is None else inherited
    root=Path(root).resolve()
    source=root/'toolchain'
    launcher=root/'portable-tools/gcc.exe'
    if not (source/'bin/gcc.exe').is_file() or not launcher.is_file():
        raise ValueError('便携编译器或路径启动器缺失')
    visible=source
    alias=None
    if not str(source).isascii():
        parent=Path(tempfile.mkdtemp(prefix='luce-gcc-'))
        visible_parent=str(parent)
        if not visible_parent.isascii():
            function=ctypes.WinDLL('kernel32',use_last_error=True).GetShortPathNameW
            function.argtypes=(ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32)
            function.restype=ctypes.c_uint32
            buffer=ctypes.create_unicode_buffer(32768)
            count=function(str(parent),buffer,len(buffer))
            if not count or count >= len(buffer) or not buffer.value.isascii():
                parent.rmdir()
                raise ValueError('无法建立英文编译器路径；请将运行包解压到纯英文目录')
            visible_parent=buffer.value
        alias=parent/'toolchain'
        environment=dict(inherited,LUCE_JUNCTION_LINK=str(alias),LUCE_JUNCTION_SOURCE=str(source))
        powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        command='New-Item -ItemType Junction -Path $env:LUCE_JUNCTION_LINK -Target $env:LUCE_JUNCTION_SOURCE -ErrorAction Stop | Out-Null'
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',command],
                              env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
        if result.returncode or not alias.is_junction() or alias.resolve()!=source:
            raise ValueError('无法建立编译器路径映射；未使用系统其他gcc替代')
        visible=Path(visible_parent)/'toolchain'

        def cleanup():
            # Non-recursive removal of our own link only; never traverse or
            # delete the compiler target. Normal atexit follows worker shutdown.
            try:
                if alias.is_junction() and alias.resolve()==source:
                    alias.rmdir()
                parent.rmdir()
            except OSError:
                pass
        atexit.register(cleanup)
    real=visible/'bin/gcc.exe'
    # libexec compiler children also need the bundled runtime DLL directory.
    path=os.pathsep.join((str(launcher.parent),str(real.parent),inherited.get('PATH','')))
    return dict(PATH=path,LUCE_PORTABLE_GCC=str(real)), dict(
        launcher=str(launcher),original_compiler=str(source/'bin/gcc.exe'),invoked_compiler=str(real),
        temporary_junction=str(alias) if alias else None,
        extra_driver_flag='-no-canonical-prefixes',
        note='只改变编译器定位路径；保留原编译参数，不改模型程序或源案例')
