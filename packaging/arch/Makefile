# Arch packaging for the Spektrum Programmer work.
#
#   make dist    -> build the source tarballs
#   make build   -> build + install the native package (spmlinux)
#   make wine    -> build + install the Wine package (official app)
#   make all     -> both
#   make clean   -> remove build artefacts

DIST := dist
SPM_PKG := spmlinux
WINE_PKG := spektrum-programmer

.PHONY: all dist build wine clean

all: build wine

dist:
	./mkdist.sh

build: dist
	cp $(DIST)/spmlinux-*.tar.gz $(SPM_PKG)/
	cd $(SPM_PKG) && makepkg -si --noconfirm

wine: dist
	cp $(DIST)/spektrum-programmer-*.tar.gz $(WINE_PKG)/
	cd $(WINE_PKG) && makepkg -si --noconfirm

clean:
	rm -rf $(DIST) $(SPM_PKG)/src $(SPM_PKG)/pkg $(SPM_PKG)/*.tar.gz \
	       $(SPM_PKG)/src $(WINE_PKG)/src $(WINE_PKG)/pkg $(WINE_PKG)/*.tar.gz \
	       $(SPM_PKG)/.SRCINFO $(WINE_PKG)/.SRCINFO
