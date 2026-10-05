"""Offline ELF symbol evidence only; deliberately has no network implementation.

The presence of SDK UDT/punch code does not establish an endpoint, wire exchange
or a capability on any intercom. Never emits arbitrary ELF strings or file paths.
"""
import argparse
import json
from pathlib import Path
import struct

MAX_ELF_SIZE = 4 * 1024 * 1024
_SYMBOLS = {
    'udt_constructor': b'_ZN6andjoy13UdtConnectionC2ERNS_2spINS_18MyUsageEnvironmentEEEPKctj',
    'udt_forward_info': b'_ZN6andjoy10GlnkDevice13getFwdUdtInfoEiPNS_13tagFwdUdtInfoE',
    'udt_local_port': b'_ZN6andjoy13UdtConnection12getLocalPortEv',
}


def analyze_elf(data, *, confirm=False, protocol_family=None, legacy_discovery_absent=False):
    """Validate bounded ELF sections and fixed exported symbols, not byte search."""
    if confirm is not True:
        raise PermissionError('Explicit offline diagnostic confirmation required')
    if protocol_family not in ('legacy_owsp', 'r002_experimental'):
        raise ValueError('Offline UDT investigation applies only to legacy/R002')
    if legacy_discovery_absent is not True:
        raise ValueError('Absence of legacy discovery must be explicitly reported')
    if not isinstance(data, bytes) or not 52 <= len(data) <= MAX_ELF_SIZE:
        raise ValueError('ELF size outside bounds')
    if data[:4] != b'\x7fELF' or data[4] not in (1, 2) or data[5] not in (1, 2) or data[6] != 1:
        raise ValueError('Unsupported ELF identification')
    endian = '<' if data[5] == 1 else '>'
    bits = 32 if data[4] == 1 else 64
    header = struct.Struct(endian + ('HHIIIIIHHHHHH' if bits == 32 else 'HHIQQQIHHHHHH'))
    section = struct.Struct(endian + ('IIIIIIIIII' if bits == 32 else 'IIQQQQIIQQ'))
    symbol = struct.Struct(endian + ('IIIBBH' if bits == 32 else 'IBBHQQ'))
    if len(data) < 16 + header.size:
        raise ValueError('Truncated ELF header')
    fields = header.unpack_from(data, 16)
    offset, entry_size, count = fields[5], fields[10], fields[11]
    if not 0 < count <= 4096 or entry_size != section.size or offset + count * entry_size > len(data):
        raise ValueError('Invalid ELF section table')
    sections = [section.unpack_from(data, offset + index * entry_size) for index in range(count)]
    observed = {name: False for name in _SYMBOLS}
    checked = 0
    for item in sections:
        if item[1] not in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
            continue
        start, size, link, entsize = item[4], item[5], item[6], item[9]
        if link >= count or entsize != symbol.size or size % entsize or start + size > len(data):
            raise ValueError('Invalid ELF symbol table')
        strings = sections[link]
        if strings[1] != 3 or strings[4] + strings[5] > len(data):
            raise ValueError('Invalid ELF symbol string table')
        table = data[strings[4]:strings[4] + strings[5]]
        for pos in range(start, start + size, entsize):
            checked += 1
            if checked > 50000:
                raise ValueError('ELF symbol count outside bounds')
            values = symbol.unpack_from(data, pos)
            name_offset = values[0]
            info, shndx = (values[3], values[5]) if bits == 32 else (values[1], values[3])
            if name_offset >= len(table):
                raise ValueError('Invalid ELF symbol name')
            end = table.find(b'\0', name_offset, min(len(table), name_offset + 512))
            if end == -1:
                raise ValueError('Unbounded ELF symbol name')
            if info >> 4 not in (1, 2) or shndx == 0:
                continue  # Only defined global/weak symbols establish SDK code.
            name = table[name_offset:end]
            for label, expected in _SYMBOLS.items():
                if name == expected:
                    observed[label] = True
    return {'operation': 'udt_offline_symbols', 'status': 'not_validated',
            'provenance': 'offline_elf_defined_symbols', 'elf_bits': bits,
            'fixed_symbols_observed': observed, 'active_probe_available': False,
            'punch_endpoint_established': False, 'requests_sent': 0,
            'reason': 'dynamic_endpoint_and_exchange_not_established'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--confirm', action='store_true')
    parser.add_argument('--protocol-family', required=True, choices=('legacy_owsp', 'r002_experimental'))
    parser.add_argument('--legacy-discovery-absent', action='store_true')
    args = parser.parse_args()
    if args.elf.stat().st_size > MAX_ELF_SIZE:
        raise SystemExit('ELF size outside bounds')
    print(json.dumps(analyze_elf(args.elf.read_bytes(), confirm=args.confirm,
        protocol_family=args.protocol_family, legacy_discovery_absent=args.legacy_discovery_absent), indent=2))


if __name__ == '__main__':
    main()
