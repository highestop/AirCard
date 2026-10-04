#ifndef WALLET_CACHE_REMOVAL_H
#define WALLET_CACHE_REMOVAL_H

#import <Foundation/Foundation.h>

// A failed stat cannot prove absence through a protected symlink. Only a
// directory listing read completely and closed successfully can do that.
static inline NSSet<NSString *> *WalletCacheDirectoryNames(
        void *connection, NSString *path,
        int (*openDirectory)(void *, const char *, void **),
        int (*readDirectory)(void *, void *, char **),
        int (*closeDirectory)(void *, void *)) {
    void *directory = NULL;
    int status = openDirectory(connection, path.fileSystemRepresentation, &directory);
    if (status != 0 || !directory) {
        if (directory) closeDirectory(connection, directory);
        return nil;
    }
    NSMutableSet *names = [NSMutableSet set];
    BOOL complete = NO;
    for (NSUInteger index = 0; index < 8192; index++) {
        char *raw = NULL;
        if (readDirectory(connection, directory, &raw) != 0) break;
        if (!raw || !raw[0]) {
            complete = YES;
            break;
        }
        NSString *name = [NSString stringWithUTF8String:raw];
        if (!name) break;
        if (![name isEqual:@"."] && ![name isEqual:@".."]) [names addObject:name];
    }
    BOOL closed = closeDirectory(connection, directory) == 0;
    return complete && closed ? [names copy] : nil;
}

static inline NSString *WalletCacheRemovalState(BOOL moved,
                                                NSSet<NSString *> *directoryNames,
                                                NSString *leaf) {
    if (moved) return @"removed";
    if (directoryNames && ![directoryNames containsObject:leaf]) return @"absent";
    return @"unverified";
}

#endif
