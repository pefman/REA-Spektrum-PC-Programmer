# Build the ftd2xx.dll stand-in for Wine.
#
#   pacman -S mingw-w64-gcc
#   make
#
# The result is copied into the wine prefix that runs the app:
#   cp ftd2xx.dll ~/.local/share/spektrum-programmer/wineprefix/drive_c/windows/system32/
#
CC      ?= x86_64-w64-mingw32-gcc
CFLAGS  ?= -O2 -Wall -shared -o ftd2xx.dll ftd2xx.c ftd2xx.def -lws2_32

all: ftd2xx.dll

ftd2xx.dll: ftd2xx.c ftd2xx.def
	$(CC) $(CFLAGS)

clean:
	rm -f ftd2xx.dll
