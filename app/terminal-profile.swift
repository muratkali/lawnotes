// Generates app/Law Notes.terminal: the Terminal profile for the dedicated Law Notes window.
// Run: swift app/terminal-profile.swift "app/Law Notes.terminal"
// update.py replaces __LAWNOTES_COMMAND__ with the real command when it installs a version
// (as text: the key map below holds a raw Escape character that plistlib can't round-trip).
import AppKit

func rgb(_ hex: Int) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: 1)
}
func archived(_ object: Any) -> Data {
    try! NSKeyedArchiver.archivedData(withRootObject: object, requiringSecureCoding: false)
}

let font = NSFont(name: "Menlo-Regular", size: 15) ?? NSFont.monospacedSystemFont(ofSize: 15, weight: .regular)
let profile: [String: Any] = [
    "name": "Law Notes",
    "type": "Window Settings",
    "ProfileCurrentVersion": 2.07,
    // the editor's NERV palette: xterm 52 background, 224 text, 208 cursor
    "BackgroundColor": archived(rgb(0x5f0000)),
    "TextColor": archived(rgb(0xffd7d7)),
    "TextBoldColor": archived(rgb(0xffd7d7)),
    "CursorColor": archived(rgb(0xff8700)),
    "SelectionColor": archived(rgb(0x870000)),
    "Font": archived(font),
    "FontAntialias": true,
    "columnCount": 100,   // the 90-column text plus room
    "rowCount": 45,
    "CommandString": "__LAWNOTES_COMMAND__",
    // Terminal sends Shift+Return as plain Return, and Shift+Option+Left/Right the same as
    // Option+Left/Right (word moves); send what herdr and xterm send, so the editor can tell them
    // apart. Key format: $ = Shift, ~ = Option, then the key's hex code (F702 Left, F703 Right).
    "keyMapBoundKeys": ["$000D": "\u{1b}[13;2u", "$~F702": "\u{1b}[1;4D", "$~F703": "\u{1b}[1;4C",
                        "~F700": "\u{1b}[1;3A", "~F701": "\u{1b}[1;3B"],  // Option+Up/Down: top/bottom
    "RunCommandAsShell": true,
    "shellExitAction": 1,  // close the window when Law Notes quits normally
    "useOptionAsMetaKey": true,  // Option+Enter, Option+arrows
    "ShowActiveProcessInTitle": false,
    "ShowCommandKeyInTitle": false,
    "ShowShellCommandInTitle": false,
    "ShowWindowSettingsNameInTitle": true,
    "ShowDimensionsInTitle": false,
    "ShowTTYNameInTitle": false,
    "WindowTitle": "",
]
let data = try! PropertyListSerialization.data(fromPropertyList: profile, format: .xml, options: 0)
try! data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
