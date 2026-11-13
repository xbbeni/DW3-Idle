# The browser build

`tools/build_web.py` builds the port for the browser with Emscripten
(WebAssembly, WebGL 2, SDL3's Emscripten port) into `build-web/`. GitHub
Actions runs it on every push (`.github/workflows/web.yml`).

It is not playable yet. Done so far:

- The game's C compiles to WebAssembly with build.py's IR rewrites (returns
  widened, the R3000's divisions); the stages' symbols are prefixed on the IR
  instead of with objcopy.
- The shaders get a GLSL ES 3.00 header in place of `#version 330 core`, and
  the context asked for is OpenGL ES 3.0 (WebGL 2).
- extract.c's Windows calls (wide paths, free space) have web versions.
- **The game's tables are out of dw3.wasm** (tools/web_fill.py). The build
  has no disc to scan, so it takes the list from the port's release at the
  same game code (`UPSTREAM_TAG` in the workflow): that exe's debug info
  names the variable each of its dw2003.fill's bytes is in, and the wasm's
  link map says where the variable went. Those bytes are zeroed and listed in
  build-web/dw3.fill; platform/fill.c reads them back from the player's disc
  at startup and checks their crc. The build fails if any is left.
  When the game code moves past that release, bump `UPSTREAM_TAG` with it.

To do:

1. **The main loop.** The game never returns from psx_main; the build uses
   ASYNCIFY, and VSync's wait (platform/etc.c) has to yield to the browser
   (emscripten_sleep) instead of SDL_DelayPrecise.
2. **The overlays' variables.** build.py puts them in COFF sections
   (`.data$dw3m1`...) that platform/memory.c resets on each overlay load;
   WebAssembly has no such sections, so they need another way to be found.
3. **Files.** The disc image chosen in the page, extracted into IndexedDB
   (IDBFS) as `data/`, and the memory card saved there.
4. **The page.** A file picker for the disc, the canvas, and the sound
   started on the first click (browsers require it).
5. crash.c (Windows crash reports) is left out.
