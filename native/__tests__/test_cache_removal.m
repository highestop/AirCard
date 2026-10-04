#import "../cache_removal.h"
#include <assert.h>

static int OpenStatus, ReadStatus, CloseStatus, Reads, Closes;
static BOOL Endless, EmptyTerminator, FailAfterFirst, EmptyDirectory;
static int Open(void *connection, const char *path, void **directory) {
    (void)connection; (void)path;
    *directory = OpenStatus == 0 ? &Reads : NULL;
    return OpenStatus;
}
static int Read(void *connection, void *directory, char **entry) {
    (void)connection; (void)directory;
    *entry = (Reads++ == 0 || Endless) ? "FrontFace" : EmptyTerminator ? "" : NULL;
    if (EmptyDirectory) *entry = NULL;
    return FailAfterFirst && Reads > 1 ? 1 : ReadStatus;
}
static int Close(void *connection, void *directory) {
    (void)connection; (void)directory;
    Closes++;
    return CloseStatus;
}
static NSSet *Listing(void) {
    Reads = Closes = 0;
    return WalletCacheDirectoryNames(NULL, @"link", Open, Read, Close);
}

int main(void) {
    @autoreleasepool {
        NSSet *names = Listing();
        assert([names containsObject:@"FrontFace"] && Closes == 1);
        assert([WalletCacheRemovalState(NO, names, @"PlaceHolder") isEqual:@"absent"]);
        assert([WalletCacheRemovalState(NO, names, @"FrontFace") isEqual:@"unverified"]);
        assert([WalletCacheRemovalState(YES, nil, @"FrontFace") isEqual:@"removed"]);
        assert([WalletCacheRemovalState(NO, nil, @"Preview") isEqual:@"unverified"]);
        EmptyTerminator = YES;
        assert(Listing() != nil && Closes == 1);
        EmptyTerminator = NO;
        EmptyDirectory = YES;
        assert(Listing().count == 0);
        EmptyDirectory = NO;
        FailAfterFirst = YES;
        assert(Listing() == nil && Closes == 1 && Reads == 2);
        FailAfterFirst = NO;
        OpenStatus = 8;  // Missing or inaccessible parent is not an empty listing.
        assert(Listing() == nil && Closes == 0);
        OpenStatus = 0; ReadStatus = 1;
        assert(Listing() == nil && Closes == 1);
        ReadStatus = 0; CloseStatus = 1;
        assert(Listing() == nil && Closes == 1);
        CloseStatus = 0; Endless = YES;
        assert(Listing() == nil && Closes == 1 && Reads == 8192);
        puts("Cache removal evidence and complete directory listing checks passed.");
    }
    return 0;
}
