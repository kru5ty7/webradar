"""Regression test for /proc/<pid>/maps parsing on Linux.

CS2 installs to ".../Counter-Strike Global Offensive/..." — a path with spaces.
The module-discovery code must split maps lines with maxsplit=5 so the pathname
(the 6th field) stays intact; a plain str.split() shatters it at the spaces and
the "is this libengine2.so?" substring check then never matches.
"""

# A realistic maps snippet: the CS2 install dir has two spaces, and shared
# objects can carry a trailing " (deleted)" after a game update.
MAPS = (
    "7f4178000000-7f4178100000 r--p 00000000 08:02 123  "
    "/home/u/.local/share/Steam/steamapps/common/Counter-Strike Global Offensive"
    "/game/bin/linuxsteamrt64/libengine2.so\n"
    "7f4178d64000-7f4178f9c000 r--p 00abc000 08:02 124  "
    "/home/u/.local/share/Steam/steamapps/common/Counter-Strike Global Offensive"
    "/game/csgo/bin/linuxsteamrt64/libclient.so (deleted)\n"
    "7ffd00000000-7ffd00021000 rw-p 00000000 00:00 0 \n"  # anonymous, no path
)


def find_module(maps_text, needle):
    """Mirror the fixed parse: (start, offset, perms, path) for a module."""
    for line in maps_text.splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) < 6 or needle not in parts[5]:
            continue
        path = parts[5].removesuffix(" (deleted)").strip()
        start = int(parts[0].split("-")[0], 16)
        offset = int(parts[2], 16)
        return start, offset, parts[1], path
    return None


def test_spaced_path_engine_found():
    hit = find_module(MAPS, "libengine2.so")
    assert hit is not None, "libengine2.so must be found despite spaces in the path"
    start, offset, perms, path = hit
    assert start == 0x7F4178000000
    assert offset == 0
    assert path.endswith("linuxsteamrt64/libengine2.so"), path
    assert " " in path, "the spaced install dir must survive parsing"


def test_deleted_suffix_stripped():
    hit = find_module(MAPS, "client.so")
    assert hit is not None
    _, _, _, path = hit
    assert path.endswith("libclient.so"), path
    assert "(deleted)" not in path


def test_old_plain_split_would_have_failed():
    # Documents the bug: plain split() puts only ".../Counter-Strike" in parts[5].
    line = MAPS.splitlines()[0]
    assert "libengine2.so" not in line.split()[5]
    assert "libengine2.so" in line.split(maxsplit=5)[5]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("all maps-parse tests passed")
