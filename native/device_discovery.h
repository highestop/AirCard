#ifndef WALLET_DEVICE_DISCOVERY_H
#define WALLET_DEVICE_DISCOVERY_H

#import <Foundation/Foundation.h>

// Notification options for AMDeviceNotificationSubscribeWithOptions.
//
// `NotificationOptionEnableRemoteXPC` is deliberately absent. The helper reaches
// devices over usbmuxd (AMDeviceConnect, AMDeviceSecureStartService), so the
// mux transport is the one that has to be requested. On macOS 27 (26A5388g)
// enabling RemoteXPC does not add to that transport, it replaces it: a
// cable-connected, paired iPhone fires no connected callback at all, only
// detach callbacks with a NULL device. Every enumeration then reported "No
// iPhone found" and the card scanner reported "iPhone not found", even though
// ioreg, usbmuxd and devicectl all listed the device. Removing the option
// returns the device immediately on the same machine.
//
// Wi-Fi paired devices stay covered: usbmuxd publishes those over the same mux
// transport, and `NotificationOptionSearchForWiFiPairableDevices` is off here
// because this helper never pairs a device over the network.
static inline NSDictionary *WalletDeviceNotificationOptions(BOOL directConnectionsOnly) {
    return @{
        @"NotificationOptionSearchForPairedDevices": @YES,
        @"NotificationOptionSearchForPairedDevicesViaDirectConnectionsOnly":
            @(directConnectionsOnly),
        @"NotificationOptionSearchForWiFiPairableDevices": @NO,
        @"NotificationOptionEnableUSBMux": @YES,
    };
}

// AMDeviceGetInterfaceType reports USB as 1 and the network interface as 2.
// An unrecognized interface must never authorize a USB-only operation.
static inline NSString *WalletDeviceTransport(unsigned int interfaceType) {
    switch (interfaceType) {
        case 1: return @"usb";
        case 2: return @"network";
        default: return @"unknown";
    }
}

static inline BOOL WalletDeviceUsesUSB(unsigned int interfaceType) {
    return interfaceType == 1;
}

// usbmuxd and MobileDevice can report the same UDID with different casing.
// Match the requested identity without changing persisted IDs or accepting a
// network connection for a USB-only operation.
static inline BOOL WalletDeviceMatchesTarget(unsigned int interfaceType,
                                             NSString *identifier,
                                             NSString *target) {
    return WalletDeviceUsesUSB(interfaceType) && identifier.length && target.length &&
        [identifier caseInsensitiveCompare:target] == NSOrderedSame;
}

static inline NSString *WalletDeviceSessionState(BOOL connected, int paired,
                                                int validation, int session) {
    if (!connected) return @"unavailable";
    if (paired == 0) return @"unpaired";
    if (paired == 1 && validation == 0 && session == 0) return @"ready";
    return @"unavailable";
}

// Keep each transport's live connection until enumeration finishes. Collapsing
// too early would lose the network entry if a preferred USB handle detaches.
static inline void WalletUpdateDeviceConnection(
        NSMutableDictionary<NSValue *, NSDictionary *> *connections,
        NSValue *handle, unsigned int message, NSDictionary *entry) {
    if (!handle) return;
    if (message == 1 && [entry[@"udid"] length])
        connections[handle] = [entry copy];
    else if (message == 2)
        [connections removeObjectForKey:handle];
}

static inline NSInteger WalletDevicePreference(NSDictionary *entry) {
    NSString *transport = entry[@"transport"];
    NSInteger preference = [transport isEqual:@"usb"] ? 20
        : [transport isEqual:@"network"] ? 10 : 0;
    NSString *session = entry[@"session_state"];
    return preference + ([session isEqual:@"ready"] ? 2
        : [session isEqual:@"unpaired"] ? 1 : 0);
}

static inline NSArray<NSDictionary *> *WalletPreferredDevices(
        NSDictionary<NSValue *, NSDictionary *> *connections) {
    NSMutableDictionary<NSString *, NSDictionary *> *preferred =
        [NSMutableDictionary dictionary];
    for (NSDictionary *entry in connections.allValues) {
        NSString *identifier = [entry[@"udid"] lowercaseString];
        if (!identifier.length) continue;
        NSDictionary *previous = preferred[identifier];
        if (!previous || WalletDevicePreference(entry) >
                         WalletDevicePreference(previous))
            preferred[identifier] = entry;
    }
    NSArray *identifiers = [preferred.allKeys sortedArrayUsingSelector:
                            @selector(compare:)];
    NSMutableArray *result = [NSMutableArray arrayWithCapacity:identifiers.count];
    for (NSString *identifier in identifiers)
        [result addObject:preferred[identifier]];
    return result;
}

#endif
