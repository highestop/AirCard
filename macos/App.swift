import AppKit
import SwiftUI

@MainActor
final class ApplicationDelegate: NSObject, NSApplicationDelegate {
    let model = WalletModel()
    private var allowingQuit = false
    private var waitingForQuit = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        model.start()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if allowingQuit { return .terminateNow }
        if waitingForQuit { return .terminateLater }
        waitingForQuit = true
        Task {
            await model.shutdown()
            allowingQuit = true
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct SkinnerApp: App {
    @NSApplicationDelegateAdaptor(ApplicationDelegate.self) private var delegate

    var body: some Scene {
        Window("Apple Wallet Card Skinner", id: "wallet") {
            WalletWorkspace(model: delegate.model)
                .frame(minWidth: 980, minHeight: 680)
                .environment(\.locale, Locale(identifier: "zh_CN"))
        }
        .defaultSize(width: 1180, height: 820)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button("关于 Apple Wallet Card Skinner") {
                    NSApp.orderFrontStandardAboutPanel(options: [
                        .applicationName: "Apple Wallet Card Skinner",
                        .applicationVersion: "1.0",
                        .credits: NSAttributedString(string: "本地运行的 Apple Wallet 卡面工具")
                    ])
                }
            }
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .help) {
                Button("使用说明") {
                    let alert = NSAlert()
                    alert.messageText = "给 Wallet 卡片换个新面貌"
                    alert.informativeText = "1. 通过 USB 连接 iPhone，解锁并信任此 Mac。\n2. 点击“扫描卡片”，在 iPhone 的 Wallet 中逐张打开需要修改的卡片。\n3. 选择图片或编辑构图，检查本地预览。\n4. 点击“写入卡面”，完成后彻底关闭并重新打开 Wallet。\n\n扫描只识别卡片，不会下载 iPhone 原卡面。移除本地记录不会删除 iPhone 上的卡片。"
                    alert.addButton(withTitle: "知道了")
                    alert.runModal()
                }
            }
        }
    }
}
