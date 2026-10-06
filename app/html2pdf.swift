// html2pdf: turn the HTML pandoc makes from a note into an A4 PDF, using macOS's own text system
// (no LaTeX needed). Usage: html2pdf in.html out.pdf
import AppKit

let args = CommandLine.arguments
guard args.count == 3, let html = FileManager.default.contents(atPath: args[1]) else {
    FileHandle.standardError.write("usage: html2pdf in.html out.pdf\n".data(using: .utf8)!)
    exit(2)
}
guard let text = NSAttributedString(html: html, options: [.documentType: NSAttributedString.DocumentType.html,
                                                           .characterEncoding: String.Encoding.utf8.rawValue],
                                    documentAttributes: nil) else {
    FileHandle.standardError.write("could not read the HTML\n".data(using: .utf8)!)
    exit(1)
}

let page = NSSize(width: 595.28, height: 841.89)  // A4 in points
let margin: CGFloat = 72                          // 2.54 cm
let info = NSPrintInfo()
info.paperSize = page
info.topMargin = margin; info.bottomMargin = margin; info.leftMargin = margin; info.rightMargin = margin
info.horizontalPagination = .fit
info.verticalPagination = .automatic
info.isVerticallyCentered = false
info.jobDisposition = .save
info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = URL(fileURLWithPath: args[2])

let view = NSTextView(frame: NSRect(x: 0, y: 0, width: page.width - 2 * margin, height: page.height))
view.textStorage?.setAttributedString(text)
view.isVerticallyResizable = true
view.textContainer?.widthTracksTextView = true
view.layoutManager?.ensureLayout(for: view.textContainer!)
view.sizeToFit()

let op = NSPrintOperation(view: view, printInfo: info)
op.showsPrintPanel = false
op.showsProgressPanel = false
exit(op.run() ? 0 : 1)
