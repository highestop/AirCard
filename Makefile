CLANG := xcrun clang
# Keep the USB helpers runnable on macOS 14 and newer.
MACOS_MIN := 14.0
CFLAGS := -fobjc-arc -O2 -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=$(MACOS_MIN)
FOUNDATION := -framework Foundation -framework CoreFoundation
MOBILEDEVICE := /System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice
AIRTRAFFIC := /System/Library/PrivateFrameworks/AirTrafficHost.framework/AirTrafficHost

.PHONY: all clean run test

all: build/device_helper build/airtraffic_host

run: all
	python3 aircard.py

test:
	python3 -m unittest discover -s tests -v

build:
	mkdir -p $@

build/device_helper: Sources/device_helper.m Sources/airlift_target.h Sources/device_discovery.h Sources/os_trace.h | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(MOBILEDEVICE) $< -o $@
	codesign --force --sign - $@

build/airtraffic_host: Sources/airtraffic_host.m | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(AIRTRAFFIC) $< -o $@
	codesign --force --sign - $@

clean:
	rm -rf build
