#!/usr/bin/env python3
import py_compile, os
os.chdir('/home/patricks/Schreibtisch/SOMA')
files = [
    'executive_arm/bluetooth_audio.py',
    'brain_core/voice/pipeline.py',
    'brain_core/logic_router.py',
]
ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'OK: {f}')
    except py_compile.PyCompileError as e:
        print(f'FAIL: {f}: {e}')
        ok = False
print('ALL PASSED' if ok else 'ERRORS FOUND')
