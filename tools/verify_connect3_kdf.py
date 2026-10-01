"""Offline emulation of ONLY AESSecret math, no Android app/device/network calls."""
from pathlib import Path
from io import BytesIO
import sys
import json
import struct
import hashlib
from elftools.elf.elffile import ELFFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from load_integration import load
production = load('connect3.kdf')
if len(sys.argv) != 2:
    raise SystemExit('Usage: python tools/verify_connect3_kdf.py /path/to/libqv-p2p-v2.so')
binary = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(binary).hexdigest() == '4aa8fe8a7c4895225754b0c61ad6178796357e106ba41d378085a7bee71f59d5', 'Wrong ARM64 binary'
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_TPIDR_EL0

e=ELFFile(BytesIO(binary))
u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
u.mem_map(0,0x1000000)
for s in e.iter_segments():
    if s['p_type']=='PT_LOAD':u.mem_write(s['p_vaddr'],s.data())
symbols=e.get_section_by_name('.dynsym')
for r in e.get_section_by_name('.rela.dyn').iter_relocations():
    if r['r_info_type']==1027:value=r['r_addend']
    elif r['r_info_type'] in (1025,1026,257):value=symbols.get_symbol(r['r_info_sym'])['st_value']+r['r_addend']
    else:continue
    u.mem_write(r['r_offset'],struct.pack('<Q',value))
defs={s.name:s['st_value'] for s in symbols.iter_symbols() if s['st_value']}
plt=e.get_section_by_name('.plt')['sh_addr']; rel=e.get_section_by_name('.rela.plt')
calls={plt+32+16*i:symbols.get_symbol(r['r_info_sym']).name for i,r in enumerate(rel.iter_relocations())}
heap=0xa00000
def hook(u,a,n,data):
    global heap
    if a not in calls:return
    name=calls[a]
    if name in defs:
        u.reg_write(UC_ARM64_REG_PC,defs[name]);return
    args=[u.reg_read(r) for r in [UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3]]
    result=args[0]
    if name in ('memcpy','__memcpy_chk'):u.mem_write(args[0],bytes(u.mem_read(args[1],args[2])))
    elif name in ('memset','__memset_chk'):u.mem_write(args[0],bytes([args[1]&255])*args[2])
    elif name=='malloc':result=heap;heap+=(args[0]+15)&~15
    elif name=='strncmp':result=int(bytes(u.mem_read(args[0],args[2]))!=bytes(u.mem_read(args[1],args[2])))
    elif name=='free':pass
    else:raise RuntimeError('Unexpected native call '+name)
    u.reg_write(UC_ARM64_REG_X0,result);u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_X30))
u.hook_add(UC_HOOK_CODE,hook)
u.reg_write(UC_ARM64_REG_TPIDR_EL0,0xf00000)
def run(addr,args):
    u.reg_write(UC_ARM64_REG_SP,0xff0000);u.reg_write(UC_ARM64_REG_X30,0xfffffc)
    # X0..X7 aren't contiguous constants in Unicorn: use names.
    import unicorn.arm64_const as reg
    for i,v in enumerate(args):u.reg_write(getattr(reg,'UC_ARM64_REG_X'+str(i)),v)
    u.emu_start(addr,0xfffffc,count=200000)
    assert u.reg_read(UC_ARM64_REG_PC)==0xfffffc

vectors=[]
for seed in [b'',b'A',bytes(32),bytes(range(32)),bytes(range(33)),bytes([255])*32,b'synthetic-discovery-seed']:
    u.mem_write(0x900000,b'\0'*64);u.mem_write(0x910000,seed or b'\0');u.mem_write(0x920000,b'1.0.0\0')
    run(defs['_ZN9AESSecretC1Ev'],[0x900000])
    run(defs['_ZN9AESSecret11GenerateKeyEPhRiiPKhPKci'],[0x900000,0x930000,0x940000,256,0x920000,0x910000,len(seed)])
    actual=bytes(u.mem_read(0x930000,32));assert actual==production.derive_key(seed),(seed.hex(),actual.hex(),production.derive_key(seed).hex())
    vectors.append({'seed_hex':seed.hex(),'key_hex':actual.hex()})
assert vectors == json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/connect3_kdf_native.json').read_text())
print('Native ARM64 math vs production Python:', len(vectors), 'synthetic vectors matched; no network')
