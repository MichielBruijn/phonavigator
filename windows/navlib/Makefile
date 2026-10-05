# Cross-compile TDxNavLib.dll with MinGW-w64: make (needs gcc-mingw-w64)
CFLAGS = -O2 -Wall -Wno-unused-parameter -std=gnu11
LDFLAGS = -s -shared -static-libgcc -Wl,--enable-stdcall-fixup

all: build/x64/TDxNavLib.dll build/x86/TDxNavLib.dll

build/x64/TDxNavLib.dll: navlib.c TDxNavLib.def
	mkdir -p $(@D)
	x86_64-w64-mingw32-gcc $(CFLAGS) -o $@ navlib.c TDxNavLib.def $(LDFLAGS)

build/x86/TDxNavLib.dll: navlib.c TDxNavLib.def
	mkdir -p $(@D)
	i686-w64-mingw32-gcc $(CFLAGS) -o $@ navlib.c TDxNavLib.def $(LDFLAGS)

clean:
	rm -rf build
