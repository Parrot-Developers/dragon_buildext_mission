##########
# Native #
##########

# A typical mission tree looks like:
#
#   payload
#	├── lib
#	│   ├── libfoo.so (need DT_RUNPATH=$ORIGIN)
#	│   ├── libbar.so (need DT_RUNPATH=$ORIGIN)
#	│   └── gstreamer-1.0 (optional)
#	│        └── libgstbam.so (depend on libfoo.so, need DT_RUNPATH=$ORIGIN/..)
#	└── services
#	    └── gee-service (need DT_RUNPATH=$ORIGIN/../lib)
#
# So to fullfill all dependencies the following paths are set.
#
# Note: $ORIGIN/../lib would work for lib in ./lib but lead to ugly multiple
# relative path resolution (ex: ../lib/../lib/libfoo.so) for libbar that depends
# on libfoo called from an executable with rpath=$ORIGIN/../lib
TARGET_GLOBAL_LDFLAGS := -Wl,-rpath=\$$ORIGIN:\$$ORIGIN/..:\$$ORIGIN/../lib

##########
# Python #
##########

PYTHON3_VERSION := 3.7

# Rule to create the usr/lib/python symlink early to avoid surprises if a module
# installs something in usr/lib/python before the link is created.
define python3-symlink-create
	@if [[ -e $1 && ! -L $1 ]]; then \
		echo "$1 is not a symlink, you need to clean the output directory"; \
		exit 1; \
	fi
	$(Q) mkdir -p $(dir $1)/python$(PYTHON3_VERSION)
	$(Q) ln -sf python$(PYTHON3_VERSION) $1
endef

.PHONY: python3-symlink
python3-symlink:
	$(call python3-symlink-create,$(HOST_OUT_STAGING)/$(HOST_ROOT_DESTDIR)/lib/python)
	$(call python3-symlink-create,$(TARGET_OUT_STAGING)/$(TARGET_ROOT_DESTDIR)/lib/python)

TARGET_GLOBAL_PREREQUISITES += python3-symlink
