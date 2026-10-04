"""Offline WelcomeEye ARMv7 AESSecret math, without Android/device/network calls.

Requires pyelftools and unicorn. Supply the owner's extracted libqv-p2p-v2.so.
Only allocation, memory, string and integer-division primitives are emulated.
"""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import struct
import sys

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
import unicorn.arm_const as reg

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
from load_integration import load

production = load('connect3.kdf')
if len(sys.argv) != 2:
    raise SystemExit('Usage: python tools/verify_qv_kdf_armv7.py /path/to/libqv-p2p-v2.so')
binary = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(binary).hexdigest() == (
    '210402ce70a86a7d3ab5d25cecb8393b72d682ccd8576b02dfcb8a4458346d41'
), 'Wrong WelcomeEye ARMv7 binary'

elf = ELFFile(BytesIO(binary))
emulator = Uc(UC_ARCH_ARM, UC_MODE_ARM)
emulator.mem_map(0, 0x1000000)
for segment in elf.iter_segments():
    if segment['p_type'] == 'PT_LOAD':
        emulator.mem_write(segment['p_vaddr'], segment.data())
symbols = elf.get_section_by_name('.dynsym')
for relocation in elf.get_section_by_name('.rel.dyn').iter_relocations():
    if relocation['r_info_type'] in (21, 22, 2):
        emulator.mem_write(relocation['r_offset'], struct.pack(
            '<I', symbols.get_symbol(relocation['r_info_sym'])['st_value']))
definitions = {symbol.name: symbol['st_value'] for symbol in symbols.iter_symbols()
               if symbol['st_value']}
plt = elf.get_section_by_name('.plt')['sh_addr']
calls = {plt + 32 + 16 * index: symbols.get_symbol(relocation['r_info_sym']).name
         for index, relocation in enumerate(
             elf.get_section_by_name('.rel.plt').iter_relocations())}
heap = 0xa00000


def hook(machine, address, size, data):
    global heap
    if address not in calls:
        return
    name = calls[address]
    if name in definitions:
        machine.reg_write(reg.UC_ARM_REG_PC, definitions[name])
        return
    args = [machine.reg_read(getattr(reg, 'UC_ARM_REG_R' + str(index)))
            for index in range(4)]
    result = args[0]
    if name in ('memcpy', '__memcpy_chk'):
        machine.mem_write(args[0], bytes(machine.mem_read(args[1], args[2])))
    elif name in ('memset', '__memset_chk'):
        machine.mem_write(args[0], bytes([args[1] & 255]) * args[2])
    elif name == 'malloc':
        result = heap
        heap += (args[0] + 15) & ~15
    elif name == 'strncmp':
        result = int(bytes(machine.mem_read(args[0], args[2]))
                     != bytes(machine.mem_read(args[1], args[2])))
    elif name == 'free':
        pass
    elif name in ('__aeabi_uidivmod', '__aeabi_idivmod'):
        # Inputs in this mathematical path are verified nonnegative values.
        assert args[0] < 0x80000000 and 0 < args[1] < 0x80000000
        result = args[0] // args[1]
        machine.reg_write(reg.UC_ARM_REG_R1, args[0] % args[1])
    elif name in ('__aeabi_uidiv', '__aeabi_idiv'):
        assert args[0] < 0x80000000 and 0 < args[1] < 0x80000000
        result = args[0] // args[1]
    else:
        raise RuntimeError('Unexpected native mathematical dependency: ' + name)
    machine.reg_write(reg.UC_ARM_REG_R0, result)
    machine.reg_write(reg.UC_ARM_REG_PC, machine.reg_read(reg.UC_ARM_REG_LR))


emulator.hook_add(UC_HOOK_CODE, hook)
emulator.reg_write(reg.UC_ARM_REG_C1_C0_2, 0xf << 20)
emulator.reg_write(reg.UC_ARM_REG_FPEXC, 0x40000000)


def run(address, args):
    stack = 0xff0000
    emulator.reg_write(reg.UC_ARM_REG_SP, stack)
    emulator.reg_write(reg.UC_ARM_REG_LR, 0xfffffc)
    for index, value in enumerate(args[:4]):
        emulator.reg_write(getattr(reg, 'UC_ARM_REG_R' + str(index)), value)
    for index, value in enumerate(args[4:]):
        emulator.mem_write(stack + 4 * index, struct.pack('<I', value))
    emulator.emu_start(address, 0xfffffc, count=500000)
    assert emulator.reg_read(reg.UC_ARM_REG_PC) == 0xfffffc


vectors = []
for seed in (b'', b'A', bytes(32), bytes(range(32)), bytes(range(33)),
             bytes([255]) * 32, b'synthetic-discovery-seed'):
    emulator.mem_write(0x900000, b'\0' * 64)
    emulator.mem_write(0x910000, seed or b'\0')
    emulator.mem_write(0x920000, b'1.0.0\0')
    run(definitions['_ZN9AESSecretC1Ev'], [0x900000])
    run(definitions['_ZN9AESSecret11GenerateKeyEPhRiiPKhPKci'],
        [0x900000, 0x930000, 0x940000, 256, 0x920000, 0x910000, len(seed)])
    actual = bytes(emulator.mem_read(0x930000, 32))
    assert actual == production.derive_key(seed), 'ARMv7/Python KDF mismatch'
    vectors.append({'seed_hex': seed.hex(), 'key_hex': actual.hex()})
assert vectors == json.loads((REPO / 'tests/fixtures/connect3_kdf_native.json').read_text())
print('Native WelcomeEye ARMv7 vs verified Door Connect ARM64 and Python:',
      len(vectors), 'synthetic vectors matched; no network')
