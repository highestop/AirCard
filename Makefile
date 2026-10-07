CLANG := xcrun clang
ARCH := $(shell uname -m)
SWIFT := xcrun swiftc -swift-version 5 -target $(ARCH)-apple-macosx14.0 -parse-as-library -module-cache-path build/macos/module-cache
SERVICE := macos/Errors.swift macos/Artwork.swift $(wildcard service/*.swift)
# Keep the USB helpers runnable on macOS 14 and newer.
MACOS_MIN := 14.0
CFLAGS := -fobjc-arc -O2 -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=$(MACOS_MIN)
FOUNDATION := -framework Foundation -framework CoreFoundation
MOBILEDEVICE := /System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice
AIRTRAFFIC := /System/Library/PrivateFrameworks/AirTrafficHost.framework/AirTrafficHost

.PHONY: all app native-service clean run test test-native test-macos test-service test-app

all: build/device_helper build/airtraffic_host

app:
	bash scripts/build_macos_app.sh

native-service: build/wallet_service

build/wallet_service: $(SERVICE) | build
	mkdir -p build/macos
	$(SWIFT) -O $(SERVICE) -o $@

run: app
	open "build/Apple Wallet Card Skinner.app"

test: test-native test-service test-macos

test-native:
	mkdir -p build/native-tests
	@for source in native/__tests__/*.m; do \
		target="build/native-tests/$$(basename "$$source" .m)"; \
		$(CLANG) -fobjc-arc -Wall -Wextra -Werror $(FOUNDATION) $(MOBILEDEVICE) "$$source" -o "$$target" && "$$target" || exit 1; \
	done

test-macos: | build
	mkdir -p build/macos
	$(SWIFT) macos/Errors.swift macos/Artwork.swift macos/__tests__/ArtworkTests.swift -o build/macos/artwork-tests
	build/macos/artwork-tests

test-service: | build
	mkdir -p build/macos
	$(SWIFT) -D SERVICE_TESTS $(SERVICE) service/__tests__/ServiceTests.swift -o build/macos/service-tests
	build/macos/service-tests

test-app:
	mkdir -p build/macos
	$(SWIFT) -D SERVICE_TESTS $(SERVICE) service/__tests__/ProductTests.swift -o build/macos/product-tests
	build/macos/product-tests "build/Apple Wallet Card Skinner.app"

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
