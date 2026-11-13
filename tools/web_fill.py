"""The game's tables out of the browser build, as tools/asset_scan.py takes
them out of the exe: every byte the exe's dw2003.fill lists as coming from
the player's disc is zeroed in dw3.wasm, and build-web/dw3.fill says where
platform/fill.c (web) reads it back from, at startup:

    crc <crc32 of all the pieces' bytes, in order> <count>
    <address> <length> <file under data/> <offset>

The same format as the exe's, with the wasm's addresses in place of RVAs.

The web build can't scan for them itself, as it has no disc: it takes the
list from a release of the port at the same game code (UPSTREAM: the folder
of its unzipped release, its dw2003.exe and dw2003.fill). The exe's debug
info names the variable each byte is in; the wasm's link map gives where that
variable is in the wasm. A variable whose size differs between the two is
left as it is and reported, as its layout may differ (the build fails on
it). A variable the wasm's link map doesn't list was dropped by the linker as
unused, so it isn't in the wasm at all.

    python tools/web_fill.py <upstream folder> <build-web folder>
"""
import bisect
import collections
import io
import re
import struct
import sys
import zlib
from pathlib import Path

IMAGE_BASE = 0x400000  # build.py links with --no-dynamicbase
STAGE = re.compile(r"(wstag\d+)(?:_head)?\.c$")


# --- the exe: its sections, its DWARF variables, its fill list ---

def pe_sections(b):
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    count = struct.unpack_from("<H", b, pe + 6)[0]
    opt = struct.unpack_from("<H", b, pe + 20)[0]
    symptr, nsym = struct.unpack_from("<II", b, pe + 12)
    strtab = symptr + nsym * 18
    out = {}
    for i in range(count):
        s = pe + 24 + opt + i * 40
        name = b[s:s + 8].rstrip(b"\0").decode()
        if name.startswith("/"):  # a long name, in the COFF string table
            o = strtab + int(name[1:])
            name = b[o:b.index(b"\0", o)].decode()
        vsize, rva, size, raw = struct.unpack_from("<IIII", b, s + 8)
        out[name] = b[raw:raw + min(vsize, size)]
    return out


def dwarf_info(b):
    from elftools.dwarf.dwarfinfo import DebugSectionDescriptor, DWARFInfo, DwarfConfig

    secs = pe_sections(b)

    def sec(name):
        data = secs.get(name)
        return None if data is None else DebugSectionDescriptor(io.BytesIO(data), name, None, len(data), 0)

    names = ["info", "aranges", "abbrev", "frame", "str", "loc", "ranges", "line", "pubtypes", "pubnames", "addr",
             "str_offsets", "line_str", "loclists", "rnglists", "sup", "types"]
    kw = {f"debug_{n}_sec": sec(f".debug_{n}") for n in names}
    kw["debug_frame_sec"] = None
    return DWARFInfo(config=DwarfConfig(little_endian=True, default_address_size=4, machine_arch="x86"),
                     eh_frame_sec=None, gnu_debugaltlink_sec=None, **kw)


def type_size(die):
    for _ in range(32):
        if die is None:
            return None
        if "DW_AT_byte_size" in die.attributes:
            return die.attributes["DW_AT_byte_size"].value
        if die.tag in ("DW_TAG_pointer_type", "DW_TAG_reference_type"):
            return 4  # clang leaves a pointer's size out; the exe is 32-bit
        if die.tag == "DW_TAG_array_type":
            size = type_size(die.get_DIE_from_attribute("DW_AT_type"))
            for c in die.iter_children():
                if c.tag == "DW_TAG_subrange_type":
                    if "DW_AT_count" in c.attributes:
                        size = size and size * c.attributes["DW_AT_count"].value
                    elif "DW_AT_upper_bound" in c.attributes:
                        size = size and size * (c.attributes["DW_AT_upper_bound"].value + 1)
                    else:
                        return None
            return size
        if "DW_AT_type" not in die.attributes:
            return None
        die = die.get_DIE_from_attribute("DW_AT_type")
    return None


def unit_key(path):
    """src/<path>.c or platform/<name>.c, whatever folder it was built in"""
    p = path.replace("\\", "/")
    for root in ("/src/", "/platform/"):
        if root in p:
            return root.strip("/") + "/" + p.rsplit(root, 1)[1]
    return p.rsplit("/", 1)[-1]


def exe_variables(b):
    """[(rva, size, unit, segment names it may have in the wasm)]"""
    out = []
    for cu in dwarf_info(b).iter_CUs():
        unit = unit_key(cu.get_top_DIE().attributes["DW_AT_name"].value.decode("latin1"))
        m = STAGE.search(unit)
        prefix = m.group(1).upper() + "_" if m else None
        for die in cu.iter_DIEs():
            if die.tag != "DW_TAG_variable" or "DW_AT_location" not in die.attributes or "DW_AT_name" not in die.attributes:
                continue
            v = die.attributes["DW_AT_location"].value
            if not isinstance(v, list) or len(v) != 5 or v[0] != 0x03:  # DW_OP_addr
                continue
            size = type_size(die)
            if not size:
                continue
            name = die.attributes["DW_AT_name"].value.decode("latin1")
            fn, par = None, die.get_parent()
            while par is not None and par.tag != "DW_TAG_compile_unit":
                if par.tag == "DW_TAG_subprogram" and "DW_AT_name" in par.attributes:
                    fn = par.attributes["DW_AT_name"].value.decode("latin1")
                    break
                par = par.get_parent()
            names = [f"{fn}.{name}"] if fn else [name] + ([prefix + name] if prefix else [])
            out.append((int.from_bytes(bytes(v[1:5]), "little") - IMAGE_BASE, size, unit, names))
    return sorted(out)


def read_fill(path):
    lines = path.read_text().split("\n")
    crc, count = lines[0].split()[1:3]
    runs = [(int(r, 16), int(n), src, int(o)) for r, n, src, o in (l.split() for l in lines[1:] if l.strip())]
    assert len(runs) == int(count), "dw2003.fill: count"
    return runs


def pieces(runs, variables):
    """each run split at the variables it spans: (unit, names, size, offset in it, length, src, src offset);
    the bytes outside any variable (padding between them) are counted apart"""
    starts = [v[0] for v in variables]
    out, outside = [], 0
    for rva, n, src, off in runs:
        pos, end = rva, rva + n
        while pos < end:
            i = bisect.bisect_right(starts, pos) - 1
            if i >= 0 and pos < variables[i][0] + variables[i][1]:
                vr, size, unit, names = variables[i]
                e = min(end, vr + size)
                out.append((unit, tuple(names), size, pos - vr, e - pos, src, off + pos - rva))
            else:
                j = bisect.bisect_right(starts, pos)
                e = min(end, starts[j]) if j < len(starts) and starts[j] > pos else end
                outside += e - pos
            pos = e
    return out, outside


# --- the wasm: where each variable went, and its data segments ---

MAP_IN = re.compile(r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)\s+(.+?):\(\.(data|rodata|bss)\.(.+)\)\s*$")


def wasm_unit(obj):
    p = obj.replace("\\", "/")
    if "/build-web/obj/" in p:
        return "src/" + p.rsplit("/build-web/obj/", 1)[1][:-2] + ".c"
    if "/build-web/platform/" in p:
        return "platform/" + p.rsplit("/", 1)[1][:-2] + ".c"
    return p.rsplit("/", 1)[-1]


def link_map(text):
    """(unit, segment name) -> (address, size, kind) from wasm-ld's -Map"""
    out = {}
    for line in text.splitlines():
        m = MAP_IN.match(line)
        if m:
            out[(wasm_unit(m.group(3)), m.group(5))] = (int(m.group(1), 16), int(m.group(2), 16), m.group(4))
    return out


def leb(b, p):
    v = s = 0
    while True:
        x = b[p]
        p += 1
        v |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return v, p


def data_segments(wasm):
    """[(address, file offset, length)] of the active data segments"""
    assert wasm[:4] == b"\0asm"
    p, out = 8, []
    while p < len(wasm):
        sid = wasm[p]
        size, p = leb(wasm, p + 1)
        end = p + size
        if sid == 11:
            count, q = leb(wasm, p)
            for _ in range(count):
                flags, q = leb(wasm, q)
                if flags == 1:
                    raise SystemExit("web_fill: a passive data segment; this build expects active ones")
                if flags == 2:
                    _, q = leb(wasm, q)
                assert wasm[q] == 0x41, "web_fill: a segment offset that isn't i32.const"
                addr, q = leb(wasm, q + 1)  # small positive addresses: unsigned LEB reads them right
                assert wasm[q] == 0x0B
                n, q = leb(wasm, q + 1)
                out.append((addr, q, n))
                q += n
        p = end
    return sorted(out)


def main(upstream, web):
    exe = (upstream / "dw2003.exe").read_bytes()
    runs = read_fill(upstream / "dw2003.fill")
    variables = exe_variables(exe)
    parts, outside = pieces(runs, variables)
    where = link_map((web / "link.map").read_text())
    wasm = bytearray((web / "dw3.wasm").read_bytes())
    segs = data_segments(wasm)
    seg_starts = [s[0] for s in segs]

    def file_offset(addr, n):
        i = bisect.bisect_right(seg_starts, addr) - 1
        if i < 0 or addr + n > segs[i][0] + segs[i][2]:
            return None  # not in a data segment (zeros: .bss, or packed out)
        return segs[i][1] + addr - segs[i][0]

    lines, crc, done, gone, resized, notdata = [], 0, 0, collections.Counter(), collections.Counter(), 0
    for unit, names, size, at, n, src, off in parts:
        hit = next((where[(unit, nm)] for nm in names if (unit, nm) in where), None)
        if hit is None:
            gone[unit] += n  # not linked in: wasm-ld dropped it, unused (file_table.c's FILE_SECTORS)
            continue
        addr, wsize, kind = hit
        if wsize != size:
            resized[f"{unit}:{names[0]}"] += n
            continue
        fo = file_offset(addr + at, n)
        if fo is None:
            notdata += n  # all zeros here: nothing of the game to take out
            continue
        piece = bytes(wasm[fo:fo + n])
        crc = zlib.crc32(piece, crc)
        wasm[fo:fo + n] = bytes(n)
        lines.append(f"{addr + at:x} {n} {src} {off}\n")
        done += n
    (web / "dw3.wasm").write_bytes(wasm)
    (web / "dw3.fill").write_text(f"crc {crc:08x} {len(lines)}\n" + "".join(lines))

    total = sum(n for _, n, _, _ in runs)
    report = [f"## The game's tables\n",
              f"{done} of the {total} bytes the exe takes out are zeroed in dw3.wasm and listed in dw3.fill "
              f"({len(lines)} pieces); {outside} are padding between the exe's variables, {notdata} are zeros in the wasm."]
    if gone:
        report.append(f"\nNot linked into the wasm (unused, so not in it; {sum(gone.values())} bytes): "
                      + ", ".join(f"{u} {n}" for u, n in gone.most_common(20)))
    if resized:
        report.append(f"\nLeft in the wasm, sized differently than in the exe ({sum(resized.values())} bytes): "
                      + ", ".join(f"{u} {n}" for u, n in resized.most_common(20)))
    text = "\n".join(report) + "\n"
    with open(web / "report.md", "a") as f:
        f.write("\n" + text)
    print(text)
    return sum(resized.values())


if __name__ == "__main__":
    left = main(Path(sys.argv[1]), Path(sys.argv[2]))
    if left:
        sys.exit(f"web_fill: {left} bytes of the game left in dw3.wasm (build-web/report.md)")
