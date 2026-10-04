#import "../device_discovery.h"
#include <assert.h>

// The legacy AMDevice API only reaches devices through usbmuxd, so the
// subscription has to request that transport. Adding RemoteXPC back hides every
// USB device on macOS 27, which is what this test locks out.
static void TestRequestsUsbMuxOnly(void) {
    for (NSNumber *direct in @[@NO, @YES]) {
        NSDictionary *options = WalletDeviceNotificationOptions(direct.boolValue);
        assert([options[@"NotificationOptionSearchForPairedDevices"] boolValue]);
        assert([options[@"NotificationOptionEnableUSBMux"] boolValue]);
        assert([options[@"NotificationOptionSearchForWiFiPairableDevices"] isEqual:@NO]);
        assert([options[@"NotificationOptionSearchForPairedDevicesViaDirectConnectionsOnly"]
            isEqual:direct]);
        for (NSString *key in options)
            assert([key rangeOfString:@"RemoteXPC"].location == NSNotFound);
    }
}

static void TestTransportAndUSBFilter(void) {
    assert([WalletDeviceTransport(1) isEqual:@"usb"]);
    assert([WalletDeviceTransport(2) isEqual:@"network"]);
    assert([WalletDeviceTransport(0) isEqual:@"unknown"]);
    assert([WalletDeviceTransport(UINT_MAX) isEqual:@"unknown"]);
    assert(WalletDeviceUsesUSB(1));
    assert(!WalletDeviceUsesUSB(2));
    assert(!WalletDeviceUsesUSB(0));
    assert(!WalletDeviceUsesUSB(UINT_MAX));
}

static void TestTargetIdentity(void) {
    assert(WalletDeviceMatchesTarget(1, @"00008030-00ABCDEF", @"00008030-00abcdef"));
    assert(WalletDeviceMatchesTarget(1, @"00008030-00abcdef", @"00008030-00ABCDEF"));
    assert(!WalletDeviceMatchesTarget(2, @"00008030-00ABCDEF", @"00008030-00abcdef"));
    assert(!WalletDeviceMatchesTarget(0, @"00008030-00ABCDEF", @"00008030-00abcdef"));
    assert(!WalletDeviceMatchesTarget(1, @"00008030-00ABCDE0", @"00008030-00ABCDEF"));
    assert(!WalletDeviceMatchesTarget(1, nil, @"00008030-00ABCDEF"));
    assert(!WalletDeviceMatchesTarget(1, @"00008030-00ABCDEF", nil));
    assert(!WalletDeviceMatchesTarget(1, @"", @""));
}

static void TestSessionStates(void) {
    assert([WalletDeviceSessionState(YES, 1, 0, 0) isEqual:@"ready"]);
    assert([WalletDeviceSessionState(YES, 0, -1, -1) isEqual:@"unpaired"]);
    assert([WalletDeviceSessionState(NO, 1, 0, 0) isEqual:@"unavailable"]);
    assert([WalletDeviceSessionState(YES, 1, 7, 0) isEqual:@"unavailable"]);
    assert([WalletDeviceSessionState(YES, 1, 0, 7) isEqual:@"unavailable"]);
    assert([WalletDeviceSessionState(YES, -1, 0, 0) isEqual:@"unavailable"]);
}

static NSDictionary *Device(NSString *udid, NSString *transport,
                            NSString *session) {
    return @{@"udid": udid, @"transport": transport, @"session_state": session};
}

static void TestConnectionLifecycle(void) {
    int usbHandle, networkHandle, otherHandle;
    NSValue *usb = [NSValue valueWithPointer:&usbHandle];
    NSValue *network = [NSValue valueWithPointer:&networkHandle];
    NSValue *other = [NSValue valueWithPointer:&otherHandle];
    NSDictionary *wireless = Device(@"PHONE-A", @"network", @"ready");
    NSDictionary *cable = Device(@"phone-a", @"usb", @"unpaired");
    NSMutableDictionary *connections = [NSMutableDictionary dictionary];
    WalletUpdateDeviceConnection(connections, network, 1, wireless);
    WalletUpdateDeviceConnection(connections, usb, 1, cable);
    NSArray *devices = WalletPreferredDevices(connections);
    assert(devices.count == 1);
    // Physical transport takes priority even if wireless had a working session.
    assert([devices.firstObject isEqual:cable]);

    WalletUpdateDeviceConnection(connections, usb, 2, nil);
    devices = WalletPreferredDevices(connections);
    assert(devices.count == 1);
    assert([devices.firstObject isEqual:wireless]);

    // Reversing callback order must yield the same preferred device.
    WalletUpdateDeviceConnection(connections, network, 2, nil);
    WalletUpdateDeviceConnection(connections, usb, 1, cable);
    WalletUpdateDeviceConnection(connections, network, 1, wireless);
    assert([WalletPreferredDevices(connections).firstObject isEqual:cable]);

    // A repeated arrival replaces that handle's stale session status.
    NSDictionary *readyCable = Device(@"phone-a", @"usb", @"ready");
    WalletUpdateDeviceConnection(connections, usb, 1, readyCable);
    assert(connections.count == 2);
    assert([WalletPreferredDevices(connections).firstObject isEqual:readyCable]);

    WalletUpdateDeviceConnection(connections, other, 1,
                                 Device(@"phone-b", @"unknown", @"unavailable"));
    devices = WalletPreferredDevices(connections);
    assert(devices.count == 2);
    assert([devices.lastObject[@"udid"] isEqual:@"phone-b"]);
    WalletUpdateDeviceConnection(connections, nil, 2, nil);
    WalletUpdateDeviceConnection(connections, usb, 4, nil);
    assert(connections.count == 3);
    for (NSValue *handle in @[usb, network, other])
        WalletUpdateDeviceConnection(connections, handle, 2, nil);
    assert(WalletPreferredDevices(connections).count == 0);
}

int main(void) {
    @autoreleasepool {
        TestRequestsUsbMuxOnly();
        TestTransportAndUSBFilter();
        TestSessionStates();
        TestTargetIdentity();
        TestConnectionLifecycle();
        puts("Device discovery options, USB filtering, session states, and lifecycle passed.");
    }
    return 0;
}
