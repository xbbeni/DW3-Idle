/* The browser build's game files: extracted once into the browser's Origin
   Private File System (OPFS, folder dw3data, platform/web/shell.html), and
   brought into the in-memory file system one file at a time, when the game
   first opens it. Loading all of it up front (IDBFS) kept ~900 MB in memory
   and hung the page. Nothing here outside the browser build. */
#ifdef __EMSCRIPTEN__
#include <emscripten.h>

EM_ASYNC_JS(int, web_fetch_file, (const char *path), {
    const p = UTF8ToString(path);
    const root = '/dw3/data/';
    if (!p.startsWith(root) || FS.analyzePath(p).exists) {
        return 1;
    }
    try {
        let dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('dw3data');
        const parts = p.slice(root.length).split('/');
        for (const d of parts.slice(0, -1)) {
            dir = await dir.getDirectoryHandle(d);
        }
        const file = await (await dir.getFileHandle(parts[parts.length - 1])).getFile();
        FS.mkdirTree(p.slice(0, p.lastIndexOf('/')));
        FS.writeFile(p, new Uint8Array(await file.arrayBuffer()));
        return 1;
    } catch (e) {
        return 0; /* not there: the caller's fopen fails as it would */
    }
});

int native_web_fetch(const char *path) {
    return web_fetch_file(path);
}

/* The page's speed buttons (etc.c's fast forward, x1 to x5): where
   g_ff_speed is, asked once before the game runs. The page then reads and
   writes it in memory: calling into the module while the game sleeps
   (ASYNCIFY) would break the suspended game */
extern int g_ff_speed;

EMSCRIPTEN_KEEPALIVE int *native_web_speed_address(void) {
    return &g_ff_speed;
}
#endif
