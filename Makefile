CLANG := xcrun clang
PYTHON ?= python3
# Keep the USB helpers runnable on macOS 14 and newer.
MACOS_MIN := 14.0
CFLAGS := -fobjc-arc -O2 -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=$(MACOS_MIN)
FOUNDATION := -framework Foundation -framework CoreFoundation
MOBILEDEVICE := /System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice
AIRTRAFFIC := /System/Library/PrivateFrameworks/AirTrafficHost.framework/AirTrafficHost

.PHONY: all app clean run test test-backend test-native test-web test-integration test-macos test-app

all: build/device_helper build/airtraffic_host

app:
	$(PYTHON) scripts/build_macos_app.py

run: app
	open "build/Apple Wallet Card Skinner.app"

test: test-backend test-native test-web test-integration test-macos

test-backend:
	$(PYTHON) -m unittest discover -s backend/__tests__ -t . -v

test-native:
	$(PYTHON) -m unittest discover -s native/__tests__ -t . -v

test-web:
	node --check web/app.js
	node --check web/device-state.js
	node --check web/artwork-bridge.js
	node --test web/__tests__/*.test.cjs

test-integration:
	$(PYTHON) -m unittest discover -s __tests__/integration -t . -v

test-macos: | build
	mkdir -p build/macos
	xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path build/macos/module-cache macos/BackendClient.swift macos/Artwork.swift macos/__tests__/ArtworkTests.swift -o build/macos/artwork-tests
	build/macos/artwork-tests

test-app:
	$(PYTHON) scripts/test_macos_app.py "build/Apple Wallet Card Skinner.app"

build:
	mkdir -p $@

build/device_helper: native/device_helper.m native/airlift_target.h native/device_discovery.h native/cache_removal.h native/os_trace.h | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(MOBILEDEVICE) $< -o $@
	codesign --force --sign - $@

build/airtraffic_host: native/airtraffic_host.m | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(AIRTRAFFIC) $< -o $@
	codesign --force --sign - $@

clean:
	rm -rf build
