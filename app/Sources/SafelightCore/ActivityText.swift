import Foundation

/// Plain-English lines for the activity log, following the voice rules in docs/DESIGN.md.
public enum ActivityText {
    static let commandNames: [String: String] = [
        "system.ping": "Checked the connection",
        "catalog.info": "Read Lightroom's state",
        "photos.get": "Read photo details",
        "photos.search": "Searched the catalog",
        "photos.preview": "Rendered a preview",
        "photos.rotate": "Rotated photos",
        "photos.create_virtual_copies": "Created virtual copies",
        "collections.list": "Listed collections",
        "collections.photos": "Read a collection",
        "collections.create": "Created a collection",
        "collections.add": "Added photos to a collection",
        "collections.remove_photos": "Removed photos from a collection",
        "collections.delete": "Deleted a collection",
        "metadata.set": "Changed metadata",
        "keywords.list": "Listed keywords",
        "keywords.add": "Added keywords",
        "keywords.remove": "Removed keywords",
        "develop.get": "Read Develop settings",
        "develop.apply": "Changed Develop settings",
        "develop.copy": "Copied Develop settings",
        "develop.auto_tone": "Applied Auto Tone",
        "develop.auto_white_balance": "Applied Auto White Balance",
        "develop.reset": "Reset Develop settings",
        "develop.apply_preset": "Applied a preset",
        "presets.list": "Listed presets",
        "snapshots.list": "Listed snapshots",
        "snapshots.create": "Created a snapshot",
        "snapshots.apply": "Applied a snapshot",
        "snapshots.delete": "Deleted a snapshot",
        "masks.list": "Read masks",
        "masks.adjust": "Adjusted a mask",
        "masks.create": "Created a mask",
        "masks.invert": "Inverted a mask",
        "masks.delete": "Deleted a mask",
        "export.photos": "Exported photos",
    ]

    /// MCP clients introduce themselves with technical names; show the product name.
    static let clientNames: [String: String] = [
        "claude-ai": "Claude Desktop",
        "claude-code": "Claude Code",
        "cursor-vscode": "Cursor",
        "cursor": "Cursor",
        "codex-mcp-client": "Codex",
        "codex": "Codex",
        "cli": "Terminal",
        "mcp-client": "AI client",
    ]

    public static func clientName(_ raw: String?) -> String? {
        guard let raw else { return nil }
        return clientNames[raw] ?? clientNames[raw.lowercased()] ?? raw
    }

    /// "Changed Develop settings", or the raw command name when unknown.
    public static func action(_ command: String?) -> String {
        guard let command else { return "Request" }
        return commandNames[command] ?? command
    }

    public static func line(for event: ActivityEvent) -> String {
        switch event.kind {
        case "confirmation_required":
            let count = event.affected.map { "\($0) photos" } ?? "a change"
            return "Asked for approval: \(action(event.command)) (\(count))"
        case "settings":
            return "Settings changed"
        case "approval":
            let count = event.affected.map { " (\($0) photos)" } ?? ""
            let verb = event.state == "approved" ? "Approved" : "Declined"
            return "\(verb) in Safelight: \(action(event.command))\(count)"
        default:
            let base = action(event.command)
            switch event.state {
            case "failed":
                return "\(base) failed" + (event.error.map { " (\(readableError($0)))" } ?? "")
            case "cancelled":
                return "\(base) was cancelled"
            default:
                return base
            }
        }
    }

    public static func readableError(_ code: String) -> String {
        switch code {
        case "LIGHTROOM_NOT_CONNECTED": "Lightroom Classic was not connected"
        case "LIGHTROOM_DISCONNECTED": "Lightroom Classic disconnected"
        case "READ_ONLY_MODE": "read-only mode is on"
        case "INVALID_CONFIRMATION": "approval expired or did not match"
        case "NOT_DELIVERED": "Lightroom Classic did not pick it up"
        case "UNKNOWN_COMMAND": "the plugin does not know this command"
        default: code.lowercased().replacingOccurrences(of: "_", with: " ")
        }
    }

    public enum Outcome: Sendable { case done, failed, waiting, info }

    public static func outcome(for event: ActivityEvent) -> Outcome {
        if event.kind == "confirmation_required" { return .waiting }
        if event.kind != "job" { return .info }
        return event.state == "failed" ? .failed : .done
    }
}
