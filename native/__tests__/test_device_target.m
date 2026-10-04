#import <Foundation/Foundation.h>

// Exercise the production callback using CF-retainable fixtures. The two
// MobileDevice metadata functions are replaced before including the helper;
// no notification subscription, phone session, pairing or service is started.
#define AMDeviceCopyDeviceIdentifier FixtureCopyDeviceIdentifier
#define AMDeviceGetInterfaceType FixtureGetInterfaceType
#define main UnusedDeviceHelperMain
#include "../device_helper.m"
#undef main
#include <assert.h>

CFStringRef FixtureCopyDeviceIdentifier(AMDeviceRef device) {
    NSString *identifier = ((__bridge NSDictionary *)device)[@"udid"];
    return identifier ? CFStringCreateCopy(NULL, (__bridge CFStringRef)identifier) : NULL;
}

unsigned int FixtureGetInterfaceType(AMDeviceRef device) {
    return [((__bridge NSDictionary *)device)[@"interface"] unsignedIntValue];
}

static void CheckCallback(NSDictionary *fixture, unsigned int message,
                          NSString *target, BOOL expected) {
    TargetIdentifier = target ? CFStringCreateCopy(NULL, (__bridge CFStringRef)target) : NULL;
    AMDeviceNotificationCallbackInfo info = { (__bridge AMDeviceRef)fixture, message };
    DeviceCallback(&info, NULL);
    assert((TargetDevice != NULL) == expected);
    if (TargetIdentifier)
        assert(CFEqual(TargetIdentifier, (__bridge CFStringRef)target));
    if (expected) {
        assert(TargetDevice == (__bridge AMDeviceRef)fixture);
        // Once selected, an unrelated callback cannot replace the target.
        NSDictionary *other = @{ @"udid": @"other-phone", @"interface": @1 };
        info.device = (__bridge AMDeviceRef)other;
        DeviceCallback(&info, NULL);
        assert(TargetDevice == (__bridge AMDeviceRef)fixture);
    }
    if (TargetDevice) CFRelease(TargetDevice);
    if (TargetIdentifier) CFRelease(TargetIdentifier);
    TargetDevice = NULL;
    TargetIdentifier = NULL;
}

int main(void) {
    @autoreleasepool {
        NSDictionary *usb = @{ @"udid": @"00008030-00ABCDEF", @"interface": @1 };
        CheckCallback(usb, 1, @"00008030-00abcdef", YES);
        CheckCallback(usb, 1, @"00008030-00ABCDEF", YES);
        CheckCallback(usb, 1, @"00008030-00abcde0", NO);
        CheckCallback(usb, 2, @"00008030-00abcdef", NO);
        CheckCallback(usb, 1, nil, NO);
        CheckCallback(@{ @"udid": @"00008030-00ABCDEF", @"interface": @2 },
                      1, @"00008030-00abcdef", NO);
        CheckCallback(@{ @"udid": @"00008030-00ABCDEF", @"interface": @0 },
                      1, @"00008030-00abcdef", NO);
        CheckCallback(@{ @"interface": @1 }, 1, @"00008030-00abcdef", NO);
        CheckCallback(nil, 1, @"00008030-00abcdef", NO);
        DeviceCallback(NULL, NULL);
        assert(TargetDevice == NULL);
        puts("Production USB target callback matches case variants without changing identity.");
    }
    return 0;
}
