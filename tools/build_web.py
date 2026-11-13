"""Builds the port for the browser (WebAssembly) with Emscripten, into
build-web/. The game's C goes through the same IR rewrites as build.py; the
platform layer is built with emcc and SDL3's Emscripten port.

    python tools/build_web.py            the game and the platform, linked
    python tools/build_web.py --compile  compile only (no link)

Work in progress: the browser build is not playable yet. What still differs
from the Windows build is listed in docs/web.md. Nothing of the game goes into
build-web/: the player's own disc is read in the browser.
"""
import collections
import concurrent.futures as cf
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NATIVE))
sys.path.insert(0, str(NATIVE / "tools"))
# build.py looks for llvm-mingw at import; the web build doesn't use it
os.environ.setdefault("LLVM_MINGW", str(NATIVE))
_argv, sys.argv = sys.argv, [sys.argv[0]]
import build  # noqa: E402
sys.argv = _argv

EMCC = shutil.which("emcc") or sys.exit("no emcc: activate the Emscripten SDK (emsdk_env)")
DECOMP = build.DECOMP
OUT = NATIVE / "build-web"
build.OUT = OUT
build.PATCHED = OUT / "patched"
build.GAME_CFLAGS = [f.replace(str(NATIVE / "build" / "patched"), str(build.PATCHED)) for f in build.GAME_CFLAGS]

# platform files that are Windows-only and have no web counterpart yet
SKIP_PLATFORM = {"crash.c"}
# SDL 3.4.16 built for the web (SDL_WEB: its install prefix, as the workflow
# makes it), the same version as the exe's; else Emscripten's own SDL3 port
SDL_WEB = os.environ.get("SDL_WEB")
SDL_C = [f"-I{SDL_WEB}/include"] if SDL_WEB else ["--use-port=sdl3"]
SDL_L = [f"-L{SDL_WEB}/lib", "-lSDL3"] if SDL_WEB else ["--use-port=sdl3"]
PLATFORM_CFLAGS = ["-std=c11", "-c", "-O2", "-g", "-Wall", "-Wno-unused-parameter", "-D_GNU_SOURCE", *SDL_C]
LDFLAGS = [*SDL_L, "-sUSE_WEBGL2=1", "-sMAX_WEBGL_VERSION=2", "-sALLOW_MEMORY_GROWTH=1",
           "-sINITIAL_MEMORY=64MB", "-sASYNCIFY=1", "-sASYNCIFY_STACK_SIZE=1048576", "-sSTACK_SIZE=1MB",
           "-sFORCE_FILESYSTEM=1", "-lidbfs.js", "-sEXIT_RUNTIME=0",
           "-sINVOKE_RUN=0", "-sEXPORTED_RUNTIME_METHODS=callMain,FS,ccall",
           # the game's function tables hold functions cast to other types
           # (heap.c's HEAP, the task tables...), called through K&R `()`
           # pointers: WebAssembly's call_indirect traps on any mismatch, the
           # R3000 and x86 didn't care. This makes indirect calls tolerate it.
           "-sEMULATE_FUNCTION_POINTER_CASTS=1",
           # while the web build is being brought up: function names in the
           # browser's stack traces, and Emscripten's own checks
           "--profiling-funcs", "-sASSERTIONS=1", "-sSTACK_OVERFLOW_CHECK=2",
           f"--shell-file={NATIVE / 'platform/web/shell.html'}"]

COMMON = re.compile(r"^(@\S+) = common ", re.M)
GLOBAL_DEF = re.compile(r"^@([\w.$]+) = (?!.*\b(?:internal|private)\b)(?!.*\bexternal\b)")
FUNC_DEF = re.compile(r"^define (?!.*\b(?:internal|private)\b)[^@]*@([\w.$]+)\(")


def run(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


# Game calls through an unprototyped `()` declaration that pass more
# arguments than the function takes: the R3000 ignored the extra registers,
# WebAssembly traps on the mismatched signature (wasm-ld's "function
# signature mismatch", which fails this build). The calls keep the first n.
EXTRA_ARGS = {
    "FIGHTSTG_startEnemyTurn": 0,   # normal_battle/menu.c passes &FIGHTSTG_battle
    "FIGHTSTG_startOneHpTurn": 0,   # the same
    "FIGHTSTG_startHitEffect": 2,   # normal_battle/menu.c passes the damage too
}
CALL = re.compile(r"(\bcall [^@]*@(" + "|".join(EXTRA_ARGS) + r")\()(.*)$")


def split_args(text):
    """the top-level comma-separated arguments of a call, and what follows its ')'"""
    depth, start, args = 0, 0, []
    for i, c in enumerate(text):
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            if depth == 0:
                if text[start:i].strip():
                    args.append(text[start:i].strip())
                return args, text[i:]
            depth -= 1
        elif c == "," and depth == 0:
            args.append(text[start:i].strip())
            start = i + 1
    raise ValueError(text)


WIDE_CALL = re.compile(r"^(\s*)(%[\w.]+ = )?((?:tail |musttail |notail )?call )((?:zeroext |signext |noundef )*)(i8|i16) (@[\w.$]+)\((.*)$")


def widen_calls(original, ll_text):
    """build.widen_returns makes the module's u8/s16 functions return i32;
    its own calls to them still say i8/i16, which x86 lets pass and
    WebAssembly's FixFunctionBitcasts turns into a trap (_bitcast_invalid).
    They call for the i32 and truncate it (the value the caller reads is the
    same). Calls from other modules are the same in wasm types (i32)."""
    widened = {re.match(r"[\w.$]+", w).group(0) for w in
               (l.split("@", 1)[1] for l in original.splitlines() if build.RET_DEFINE.match(l))}
    if not widened:
        return ll_text
    out, n = [], 0
    for line in ll_text.splitlines():
        m = WIDE_CALL.match(line)
        if m and m.group(6)[1:] in widened:
            indent, result, call, attrs, ty, callee, rest = m.groups()
            if result:
                n += 1
                name = result.split(" = ")[0]
                out.append(f"{indent}%__wide{n} = {call}i32 {callee}({rest}")
                out.append(f"{indent}{name} = trunc i32 %__wide{n} to {ty}")
                continue
            line = f"{indent}{call}i32 {callee}({rest}"
        out.append(line)
    return "\n".join(out) + "\n"


def trim_extra_args(ll_text):
    out = []
    for line in ll_text.splitlines():
        m = CALL.search(line)
        if m:
            args, rest = split_args(m.group(3))
            line = line[:m.start()] + m.group(1) + ", ".join(args[:EXTRA_ARGS[m.group(2)]]) + rest
        out.append(line)
    return "\n".join(out) + "\n"


def emit_ir(src):
    """The game file's IR, rewritten as build.py does (without the COFF overlay sections)"""
    rel = src.relative_to(DECOMP / "src")
    real = build.PATCHED / "src" / rel
    if not real.exists():
        real = src
    ll = OUT / "obj" / rel.with_suffix(".ll")
    ll.parent.mkdir(parents=True, exist_ok=True)
    r = run([EMCC, *build.GAME_CFLAGS, "-S", "-emit-llvm", f"-I{src.parent}", real, "-o", ll])
    if r.returncode != 0:
        return rel, None, r.stderr
    original = ll.read_text()
    ll_text = trim_extra_args(widen_calls(original, build.mips_divisions(build.widen_returns(original))))
    # common symbols become plain definitions, as build.py's overlay_sections
    # does (wasm-ld doesn't merge them with the other files' references)
    ll_text = COMMON.sub(r"\1 = ", ll_text)
    # each overlay's variables in one named segment, as build.py's COFF
    # sections: wasm-ld merges a name's segments into one and defines
    # __start_<name>/__stop_<name> around it (platform/memory.c)
    group = "g" if rel.parts[0] == "engine" else "s" if rel.as_posix().startswith(build.SUB_OVERLAYS) else "m"
    ll_text = build.overlay_sections(ll_text, group).replace(f'section ".data$dw3{group}1"', f'section "dw3_{group}"')
    ll.write_text(ll_text)
    return rel, ll, None


def prefix_stages(lls):
    """Each stage's own symbols get its name as a prefix (WSTAG931_startStage),
    as build.py does with objcopy, here on the IR. Returns the stages that
    have a setup (for native_stage_list)"""
    stages = collections.defaultdict(list)
    with_setup = []
    for ll in lls:
        m = build.STAGE.match(ll.with_suffix(".c").name)
        if m:
            stages[m.group(1)].append(ll)
    for stage, group in stages.items():
        names = set()
        for ll in group:
            for line in ll.read_text().splitlines():
                m = FUNC_DEF.match(line) or GLOBAL_DEF.match(line)
                if m:
                    names.add(m.group(1))
        if not names:
            continue
        if "setupStage" in names:
            with_setup.append(stage)
        pat = re.compile(r"@(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b(?![\w.$])")
        for ll in group:
            ll.write_text(pat.sub(lambda m: f"@{stage.upper()}_{m.group(1)}", ll.read_text()))
    return sorted(with_setup)


def stage_list(names):
    """The stages' setups, by name, for --dump-stages (platform/stagedump.c), as build.py writes it"""
    src = OUT / "stage_list.c"
    with open(src, "w") as f:
        f.writelines(f"void {n.upper()}_setupStage(void); void {n.upper()}_startStage(void);\n" for n in names)
        f.write("struct { const char *name; void (*setup)(void); void *start; } native_stage_list[] = {\n")
        f.writelines(f'    {{"{n}", {n.upper()}_setupStage, (void *){n.upper()}_startStage}},\n' for n in names)
        f.write("    {0, 0, 0},\n};\n")
    obj = OUT / "stage_list.o"
    r = run([EMCC, *PLATFORM_CFLAGS, src, "-o", obj])
    if r.returncode != 0:
        sys.exit(r.stderr)
    return obj


def compile_ir(ll):
    obj = ll.with_suffix(".o")
    r = run([EMCC, "-c", *build.GAME_OPT, "-g", "-Wno-override-module", ll, "-o", obj])
    return obj if r.returncode == 0 else None, r.stderr


def main():
    if OUT.exists() and "--clean" in sys.argv:
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    build.apply_patches()
    srcs = build.game_sources()
    jobs = os.cpu_count() or 4

    with cf.ThreadPoolExecutor(jobs) as ex:
        emitted = list(ex.map(emit_ir, srcs))
    failed = [(rel, err) for rel, ll, err in emitted if ll is None]
    for rel, err in failed:
        print(f"FAILED {rel}\n{err[:2000]}")
    if failed:
        sys.exit(f"{len(failed)} game files failed")
    lls = [ll for _, ll, _ in emitted]
    staged = prefix_stages(lls)

    with cf.ThreadPoolExecutor(jobs) as ex:
        compiled = list(ex.map(compile_ir, lls))
    bad = [(ll, err) for ll, (obj, err) in zip(lls, compiled) if obj is None]
    for ll, err in bad:
        print(f"FAILED {ll.name}\n{err[:2000]}")
    if bad:
        sys.exit(f"{len(bad)} game files failed to compile")
    game_objs = [obj for obj, _ in compiled]
    print(f"game: {len(game_objs)} files compiled")
    # a call LLVM couldn't make fit its function's signature becomes a trap
    # (<name>_bitcast_invalid) at run time: fail here instead
    nm = Path(EMCC).parent / "emnm"
    invalid = collections.Counter()
    with cf.ThreadPoolExecutor(jobs) as ex:
        for obj, syms in zip(game_objs, ex.map(lambda o: run([nm, "--defined-only", o]).stdout, game_objs)):
            for sym in re.findall(r"(\S*_bitcast_invalid)", syms):
                invalid[f"{obj.relative_to(OUT / 'obj')}: {sym}"] += 1
    if invalid:
        sys.exit("calls that would trap (bitcast_invalid):\n" + "\n".join(f"  {k}" for k in sorted(invalid)))

    platform, pfail = [], []
    for src in sorted((NATIVE / "platform").glob("*.c")):
        if src.name in SKIP_PLATFORM:
            continue
        obj = OUT / "platform" / src.with_suffix(".o").name
        obj.parent.mkdir(parents=True, exist_ok=True)
        r = run([EMCC, *PLATFORM_CFLAGS, src, "-o", obj])
        if r.returncode != 0:
            pfail.append((src.name, r.stderr))
        else:
            platform.append(obj)
    for name, err in pfail:
        print(f"FAILED platform/{name}\n{err[:3000]}")
    print(f"platform: {len(platform)} compiled, {len(pfail)} failed")
    if pfail:
        sys.exit(f"{len(pfail)} platform files failed")
    if "--compile" in sys.argv:
        return
    platform.append(stage_list(staged))

    # what nothing defines gets a stub that logs once and returns 0, as in build.py;
    # the linker names them
    stubs = OUT / "stubs.c"
    stubs.write_text("")
    objs = game_objs + platform
    html = OUT / "index.html"
    link_map = OUT / "link.map"
    for attempt in range(2):
        stub_obj = OUT / "stubs.o"
        r = run([EMCC, *PLATFORM_CFLAGS, stubs, "-o", stub_obj])
        if r.returncode != 0:
            sys.exit(r.stderr)
        rsp = OUT / "objs.rsp"
        rsp.write_text("\n".join(f'"{o}"' for o in objs + [stub_obj]))
        r = run([EMCC, f"@{rsp}", "-o", html, *LDFLAGS, f"-Wl,-Map={link_map}"])
        if r.returncode == 0:
            # a call through a prototype that differs from the definition traps
            # in WebAssembly (where x86 let it pass): list them, the build fails
            # open: debug/vibration_test.c's, a menu the game never opens
            mismatches = sorted(set(re.findall(r"function signature mismatch: ([\w$]+)", r.stderr)) - {"open"})
            if mismatches:
                print(r.stderr[:20000])
                sys.exit(f"{len(mismatches)} functions called with another signature than defined: " + ", ".join(mismatches))
            break
        missing = sorted(set(re.findall(r"undefined symbol: ([\w$]+)", r.stderr)))
        if not missing or attempt:
            sys.exit(f"link failed:\n{r.stderr[:6000]}")
        print(f"stubbing {len(missing)} undefined symbols")
        stubs.write_text("void log_once(const char *what);\n" + "".join(
            f'int {n}(void) {{ log_once("stub: {n}"); return 0; }}\n' for n in missing))
    (OUT / "report.md").write_text(
        f"# Web build\n\n{len(game_objs)} game files, {len(platform)} platform files\n\n## Stubbed\n\n"
        + "".join(f"- {line.split('(')[0].split()[-1]}\n" for line in stubs.read_text().splitlines()[1:]))
    # the game's tables out of dw3.wasm, as asset_scan.py takes them out of
    # the exe (tools/web_fill.py): UPSTREAM is the unzipped release of the
    # port at the same game code, whose exe and dw2003.fill say what they are
    upstream = os.environ.get("UPSTREAM")
    if not upstream:
        print("warning: no UPSTREAM: the game's tables are still in dw3.wasm (don't publish this build)")
    else:
        import web_fill
        left = web_fill.main(Path(upstream), OUT)
        if left:
            sys.exit(f"{left} bytes of the game left in dw3.wasm (build-web/report.md)")
    print(f"OK {html}")


if __name__ == "__main__":
    main()
